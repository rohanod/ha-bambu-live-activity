# Agents

## Update workflow

HACS installs from git tags/releases, so every user-visible change ships as a new version:

1. Make and verify the change (`python3 -m py_compile custom_components/bambu_live_activity/*.py`; `tests/` if pytest is available).
2. Bump `version` in `custom_components/bambu_live_activity/manifest.json` (semver; patch for fixes/tweaks, minor for features). Keep `hacs.json` as is.
3. Update `README.md` if behaviour or layout changed.
4. Commit: `git commit -am "Release X.Y.Z"` (or a descriptive message plus a separate release commit). Never co-author commits.
5. Tag: `git tag vX.Y.Z` (tag = manifest version with a `v` prefix).
6. Push: `git push origin main vX.Y.Z`.
7. Release: `gh release create vX.Y.Z --title "vX.Y.Z" --notes "<what changed>"`.
8. Users: HACS → update **Bambu Live Activity** → restart Home Assistant.

Notes:
- Do not commit inside the sandbox; ask for out-of-sandbox permission for git writes.
- Keep the config-entry domain `bambu_live_activity` and the Live Activity `tag` stable so existing installs update in place.
- The `test_activity` action uses a separate `_test` tag and counts against iOS's push budget; don't automate it.
- Never end (`clear_notification`) and restart an activity for transient state; every end + start can leave an extra activity on devices (Mac mirrors them). Use `get_status` to inspect.
