@echo off
cd /d "%~dp0"
py -3 -m asset_simulation.server %*
