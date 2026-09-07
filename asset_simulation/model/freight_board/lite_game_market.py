"""Three-round public market, AI decisions and settlement for Main-6C Lite."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from ..registry import sha256_json
from ..shipping_v3.types import BallastOrder
from .lite_game_support import ADVISOR_POLICY, COMPANY_COUNT, POLICIES, ROUND_NAMES, TOTAL_TURNS

class _LiteMarketMixin:
    def _company_market_row(self, indication, cid: int) -> dict[str, Any]:
        report = indication.final_trial.report()
        row = {}
        for origin in self.spec.origins:
            allocated = [
                ship for ship in report["routes"][origin]["allocation"]["ships"]
                if self.ownership[ship["ship_id"]] == cid
            ]
            positions = []
            index = {sid: i + 1 for i, sid in enumerate(self.queues[origin])}
            for ship in allocated:
                positions.append({
                    "ship_id": ship["ship_id"],
                    "queue_rank": index.get(ship["ship_id"]),
                    "class_id": ship["class_id"],
                    "status": ship["status"],
                    "load_factor": ship["load_factor"],
                    "assigned_cargo_bbl": ship["assigned_cargo_bbl"],
                })
            queue_ranks = [position["queue_rank"] for position in positions
                           if position["queue_rank"] is not None]
            loaded_ranks = [position["queue_rank"] for position in positions
                            if position["queue_rank"] is not None
                            and position["assigned_cargo_bbl"] > 0]
            row[origin] = {
                "offered": len(allocated),
                "loaded": sum(x["assigned_cargo_bbl"] > 0 for x in allocated),
                "full": sum(x["status"] == "full" for x in allocated),
                "marginal": sum(x["status"] == "marginal" for x in allocated),
                "unused": sum(x["status"] == "unused" for x in allocated),
                "queue_first": min(queue_ranks) if queue_ranks else None,
                "queue_last": max(queue_ranks) if queue_ranks else None,
                "loaded_queue_last": max(loaded_ranks) if loaded_ranks else None,
                "positions": positions,
            }
        return row


    def _market_view(self, indication) -> dict[str, Any]:
        bilateral = indication.report()
        report = indication.final_trial.report()
        routes = {}
        for origin in self.spec.origins:
            route = report["routes"][origin]
            allocation = route["allocation"]
            evidence = route["availability_evidence"]
            by_basis: dict[str, dict[str, int]] = {}
            for item in evidence:
                basis = item["basis"]
                bucket = by_basis.setdefault(basis, {"ship_count": 0, "capacity_bbl": 0})
                bucket["ship_count"] += 1
                bucket["capacity_bbl"] += item["capacity_bbl"]
            queue_index = {ship_id: index + 1 for index, ship_id in enumerate(self.queues[origin])}
            loaded_queue_ranks = [
                queue_index[ship["ship_id"]]
                for ship in allocation["ships"]
                if ship["assigned_cargo_bbl"] > 0 and ship["ship_id"] in queue_index
            ]
            routes[origin] = {
                "cargo_bbl": route["trial_cargo_bbl"],
                "loaded_bbl": allocation["loaded_bbl"],
                "price_real_usd_per_bbl": route["net_service_value_real_usd_per_bbl"],
                "benchmark_real_tce": route["route_benchmark_real_tce"],
                "benchmark_nominal_tce": route["route_benchmark_nominal_tce"],
                "queue_ship_count": len(self.queues[origin]),
                "loading_cutoff_queue_rank": max(loaded_queue_ranks) if loaded_queue_ranks else None,
                "full_ship_count": allocation["full_ship_count"],
                "marginal_ship_id": allocation["marginal_ship_id"],
                "marginal_load_factor": allocation["marginal_load_factor"],
                "unused_ship_count": allocation["unused_ship_count"],
                "unserved_cargo_bbl": allocation["unserved_trial_cargo_bbl"],
                "capacity_shortfall": allocation["unserved_trial_cargo_bbl"] > 0,
                "capacity_evidence": by_basis,
            }
        destination = report["inventory"]["destination"]
        future = destination["future_path"][:6]
        sellers = bilateral["seller_offers"]
        companies = {
            str(cid): self._company_market_row(indication, cid)
            for cid in range(COMPANY_COUNT)
        }
        return {
            "routes": routes,
            "seller_offers": sellers,
            "destination_inventory": {
                "opening": destination["opening"],
                "future_path": future,
            },
            "buyer": {
                "natural_total_bbl": bilateral["natural_total_bbl"],
                "final_total_bbl": bilateral["final_total_bbl"],
                "inventory_draw_vs_natural_bbl": bilateral["inventory_draw_vs_natural_bbl"],
                "inventory_build_vs_natural_bbl": bilateral["inventory_build_vs_natural_bbl"],
                "source_reallocated_bbl": bilateral["source_reallocated_bbl"],
            },
            "companies": companies,
        }


    def _route_scores(self, market: Mapping[str, Any], policy: Mapping[str, float]) -> dict[str, float]:
        prices = {origin: max(1e-9, market["routes"][origin]["price_real_usd_per_bbl"])
                  for origin in self.spec.origins}
        mean_price = sum(prices.values()) / len(prices)
        scores = {}
        for origin in self.spec.origins:
            route = market["routes"][origin]
            evidence = route["capacity_evidence"]
            future_capacity = sum(
                evidence.get(basis, {}).get("capacity_bbl", 0)
                for basis in ("committed", "proposed")
            )
            prompt_capacity = max(1, sum(
                evidence.get(basis, {}).get("capacity_bbl", 0)
                for basis in ("trial", "reserve")
            ))
            price_signal = math.log(prices[origin] / mean_price)
            future_pressure = future_capacity / prompt_capacity
            scores[origin] = price_signal - policy["future_weight"] * 0.08 * future_pressure
        origins = self.spec.origins
        if len(origins) == 2:
            scores[origins[0]] += policy["bias"]
            scores[origins[1]] -= policy["bias"]
        return scores


    def _counts_from_action(self, cid: int, action: Mapping[str, Any]) -> dict[str, Any]:
        eligible = self._eligible(cid)
        return {
            "offer_counts": {origin: len(action["offers"][origin]) for origin in self.spec.origins},
            "ballast_counts": {
                origin: sum(target == origin for target in action["ballast_targets"].values())
                for origin in self.spec.origins
            },
            "wait_count": len(eligible["east_asia_idle"]) - len(action["ballast_targets"]),
        }


    def _policy_counts(self, cid: int, policy: Mapping[str, float]) -> tuple[dict[str, Any], list[str], str]:
        if self.public_market is None:
            raise ValueError("no public market to advise against")
        eligible = self._eligible(cid)
        previous = self.company_actions[cid]
        scores = self._route_scores(self.public_market, policy)
        offer_counts = {}
        reasons = []
        company_market = self.public_market["companies"][str(cid)]
        for origin in self.spec.origins:
            n = len(eligible["prompt"][origin])
            prev = len(previous["offers"][origin])
            unused = company_market[origin]["unused"]
            target = n
            if unused:
                target = max(0, n - max(1, round(unused * policy["withdraw_unused"])))
                reasons.append(f"{origin} 有 {unused} 条本公司后排未装船，建议适度撤出报价压力。")
            elif scores[origin] < -0.12 and policy["stickiness"] < 0.6:
                target = max(0, n - max(1, round(n * 0.15))) if n else 0
            if policy["stickiness"] >= 0.7 and self.round_no > 1:
                target = round(policy["stickiness"] * prev + (1 - policy["stickiness"]) * target)
            offer_counts[origin] = min(n, max(0, target))

        idle = len(eligible["east_asia_idle"])
        origins = self.spec.origins
        delta = scores[origins[0]] - scores[origins[1]]
        share_first = 0.5 + 0.43 * math.tanh(3.5 * delta)
        mobilize = 1.0 if max(scores.values()) > -0.08 else max(0.45, 1 - 0.45 * policy["stickiness"])
        moving = min(idle, max(0, round(idle * mobilize)))
        first = min(moving, max(0, round(moving * share_first)))
        ballast_counts = {origins[0]: first, origins[1]: moving - first}
        if idle:
            best = max(origins, key=lambda origin: (scores[origin], -origins.index(origin)))
            route = self.public_market["routes"][best]
            future = route["capacity_evidence"].get("committed", {}).get("ship_count", 0) + \
                     route["capacity_evidence"].get("proposed", {}).get("ship_count", 0)
            reasons.append(
                f"空放方向偏向 {best}；当前相对评分更高，但已知/拟议未来到船约 {future} 条。"
            )
        risk_origin = max(origins, key=lambda origin: self.public_market["routes"][origin]["benchmark_real_tce"])
        risk = f"主要风险：其他公司也能看到 {risk_origin} 的高报价，第三轮可能出现同步拥挤。"
        return {"offer_counts": offer_counts, "ballast_counts": ballast_counts}, reasons, risk


    def advisor(self) -> dict[str, Any] | None:
        if self.human_company_id is None or self.phase != "round":
            return None
        counts, reasons, risk = self._policy_counts(self.human_company_id, ADVISOR_POLICY)
        return {
            "label": "AI Shipping Advisor",
            "suggested_action": counts,
            "reasons": reasons or ["当前没有强烈的撤单或改道信号，建议保持较均衡的暴露。"],
            "risk": risk,
            "future_seed_access": False,
        }


    def _expand_counts(self, cid: int, counts: Mapping[str, Any]) -> dict[str, Any]:
        eligible = self._eligible(cid)
        if set(counts.get("offer_counts", {})) != set(self.spec.origins):
            raise ValueError("offer_counts must identify both routes")
        if set(counts.get("ballast_counts", {})) != set(self.spec.origins):
            raise ValueError("ballast_counts must identify both routes")
        previous = self.company_actions[cid]
        offers = {}
        for origin in self.spec.origins:
            count = counts["offer_counts"][origin]
            if type(count) is not int or not 0 <= count <= len(eligible["prompt"][origin]):
                raise ValueError("invalid prompt offer count")
            queue_rank = {sid: i for i, sid in enumerate(self.queues[origin])}
            prior = [sid for sid in previous["offers"][origin] if sid in eligible["prompt"][origin]]
            prior.sort(key=lambda sid: (queue_rank.get(sid, 10**9), sid))
            selected = prior[:count]
            if len(selected) < count:
                others = [sid for sid in eligible["prompt"][origin] if sid not in selected]
                others.sort()
                selected.extend(others[:count-len(selected)])
            offers[origin] = tuple(selected)

        requested = {origin: counts["ballast_counts"][origin] for origin in self.spec.origins}
        if any(type(v) is not int or v < 0 for v in requested.values()) or sum(requested.values()) > len(eligible["east_asia_idle"]):
            raise ValueError("invalid East Asia ballast counts")
        available = list(eligible["east_asia_idle"])
        targets: dict[int, str] = {}
        # Preserve same target first; a changed target is allowed but consumes the
        # current market round and will be visible in the next public indication.
        for origin in self.spec.origins:
            keep = [sid for sid, target in previous["ballast_targets"].items()
                    if target == origin and sid in available]
            keep.sort()
            for sid in keep[:requested[origin]]:
                targets[sid] = origin
                available.remove(sid)
        for origin in self.spec.origins:
            need = requested[origin] - sum(target == origin for target in targets.values())
            if need <= 0:
                continue
            compatible = []
            service_classes = {service.class_id for service in self.spec.market.physical.lane(origin).services}
            ship_map = {s.ship_id: s for s in self.snapshot.opened_market.ships}
            for sid in available:
                if ship_map[sid].class_id in service_classes:
                    compatible.append(sid)
            for sid in compatible[:need]:
                targets[sid] = origin
                available.remove(sid)
            if sum(target == origin for target in targets.values()) != requested[origin]:
                raise ValueError("requested ballast count is not compatible with route classes")
        return {"offers": offers, "ballast_targets": targets}


    def _ai_action(self, cid: int) -> dict[str, Any]:
        counts, _, _ = self._policy_counts(cid, POLICIES[cid])
        return self._expand_counts(cid, counts)


    def _update_queues(self, actions: Mapping[int, Mapping[str, Any]]) -> None:
        for origin in self.spec.origins:
            offered = {sid for action in actions.values() for sid in action["offers"][origin]}
            kept = [sid for sid in self.queues[origin] if sid in offered]
            joined = sorted(offered - set(kept), key=lambda sid: self._queue_key(origin, sid))
            self.queues[origin] = kept + joined


    def _aggregate_ballasts(self, actions: Mapping[int, Mapping[str, Any]]) -> tuple[BallastOrder, ...]:
        rows = []
        used = set()
        for cid in range(COMPANY_COUNT):
            for sid, origin in sorted(actions[cid]["ballast_targets"].items()):
                if sid in used:
                    raise ValueError("a ship cannot be ballasted by two companies")
                if self.ownership.get(sid) != cid:
                    raise ValueError("company attempted to move another owner's ship")
                used.add(sid)
                rows.append(BallastOrder(sid, origin))
        return tuple(rows)


    def submit_round(self, counts: Mapping[str, Any] | None = None, *, use_advisor: bool = False,
                     keep: bool = False, round_token: str | None) -> dict[str, Any]:
        with self._lock:
            if self.phase != "round" or self.human_company_id is None:
                raise ValueError("no active human market round")
            if not isinstance(round_token, str) or round_token != self.round_token:
                raise ValueError("missing or stale game round token")
            cid = self.human_company_id
            if use_advisor:
                counts = self.advisor()["suggested_action"]
            elif keep:
                counts = self._counts_from_action(cid, self.company_actions[cid])
                counts = {"offer_counts": counts["offer_counts"], "ballast_counts": counts["ballast_counts"]}
            if counts is None:
                raise ValueError("submit explicit counts, keep, or use the advisor")
            actions = {company: self._ai_action(company) for company in range(COMPANY_COUNT) if company != cid}
            actions[cid] = self._expand_counts(cid, counts)
            self._update_queues(actions)
            ballasts = self._aggregate_ballasts(actions)
            row = self.inputs[self.turn_index]
            indication = self.market.indicate(self.queues, row["natural_cargo_plan_bbl"], ballast_orders=ballasts)
            self.company_actions = actions
            self.current_indication = indication
            self.public_market = self._market_view(indication)
            self.round_history.append({
                "round": self.round_no,
                "round_name": ROUND_NAMES[self.round_no],
                "indication_id": indication.identity,
                "market": self.public_market,
                "company_counts": {str(company): self._counts_from_action(company, action)
                                   for company, action in actions.items()},
            })
            if self.round_no < 3:
                self.round_no += 1
                return self.public_state()
            self._settle_final_round(indication, ballasts)
            return self.public_state()


    def _settle_final_round(self, indication, ballasts: Sequence[BallastOrder]) -> None:
        report = indication.final_trial.report()
        company_turn = {
            cid: {"cargo_bbl": 0, "gross_service_value_real_usd": 0.0,
                  "full": 0, "marginal": 0, "ballasts": 0}
            for cid in range(COMPANY_COUNT)
        }
        loaded_ids = set()
        for origin in self.spec.origins:
            for ship in report["routes"][origin]["allocation"]["ships"]:
                if not ship["assigned_cargo_bbl"]:
                    continue
                cid = self.ownership[ship["ship_id"]]
                loaded_ids.add(ship["ship_id"])
                company_turn[cid]["cargo_bbl"] += ship["assigned_cargo_bbl"]
                company_turn[cid]["gross_service_value_real_usd"] += ship["service_value_real_usd"]
                company_turn[cid]["full"] += ship["status"] == "full"
                company_turn[cid]["marginal"] += ship["status"] == "marginal"
        for order in ballasts:
            company_turn[self.ownership[order.ship_id]]["ballasts"] += 1
        for cid in range(COMPANY_COUNT):
            eligible = self._eligible(cid)
            prompt_ids = {sid for ids in eligible["prompt"].values() for sid in ids}
            idle_prompt = len(prompt_ids - loaded_ids)
            s = self.stats[cid]
            s["gross_service_value_real_usd"] += company_turn[cid]["gross_service_value_real_usd"]
            s["cargo_carried_bbl"] += company_turn[cid]["cargo_bbl"]
            s["full_fixtures"] += company_turn[cid]["full"]
            s["marginal_fixtures"] += company_turn[cid]["marginal"]
            s["ballast_orders"] += company_turn[cid]["ballasts"]
            s["idle_prompt_ship_turns"] += idle_prompt
        firm = self.market.lock_firm(indication)
        record = self.market.commit_firm()
        input_row = self.inputs[self.turn_index]
        turn_record = {
            "turn": self.turn_index,
            "year": input_row["year"],
            "month": input_row["month"],
            "turn_in_month": input_row["turn_in_month"],
            "commitment_id": firm.identity,
            "execution_id": record["execution_id"],
            "final_market": self.public_market,
            "company_results": {str(cid): company_turn[cid] for cid in range(COMPANY_COUNT)},
        }
        self.turn_history.append(turn_record)
        self.turn_index += 1
        self.round_no = 0
        self.snapshot = None
        self.current_indication = None
        self.phase = "game_over" if self.turn_index >= TOTAL_TURNS else "turn_complete"


    def next_turn(self, *, round_token: str | None) -> dict[str, Any]:
        with self._lock:
            if self.phase != "turn_complete":
                raise ValueError("next turn is only available after final settlement")
            if not isinstance(round_token, str) or round_token != self.round_token:
                raise ValueError("missing or stale game turn token")
            self.phase = "between_turns"
            self._open_turn()
            return self.public_state()
