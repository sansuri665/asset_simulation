# Stage6B-v3 release verification

The v3 replacement is awaiting final regression of opening-time availability normalization.
A two-turn ballast voyage committed after the previous opening can appear at h=1,
not ordinarily at h=2. Uncommitted future voyages must not be treated as missing supply.
The branch head has moved so an earlier successful workflow cannot retire v2.
Only a verified workflow for the current final head may retire the unchanged v2 ancestor.
