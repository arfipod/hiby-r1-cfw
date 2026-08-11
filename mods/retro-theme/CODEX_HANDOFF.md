# Retro Handheld integration status

Retro Handheld is now integrated into the CFW source rather than being a future
Codex handoff.

The implementation consists of:

- deterministic local resource generation in `tools/build_retro_theme.py`;
- firmware installation/verification in `tools/patch_r1_retro_theme.py`;
- all safe Retro launcher variants in `tools/patch_r1_retro_launcher.py`;
- persistent Stock/Light/Dark/Retro selection in the CFW sidecar;
- fail-open transactional bind mounts in `S90r1-cfw`;
- strict candidate re-extraction, tree comparison, size, and release gates.

Generated vendor-derived `retro/` trees must remain outside Git. Read
`docs/retro-theme.md` for the current architecture and validation procedure.
