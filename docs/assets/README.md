# docs/assets

Drop the recorded/screenshot assets here using the exact filenames below so the
main README and docs pick them up. See `docs/launch/connect-walkthrough.md` for
the full capture guide (what frame to grab for each).

Captured (live panel, rendered exactly as it appears in Claude):

- `panel-populated.png` - populated plan (dark)
- `panel-in-progress.png` - a task mid-flight + some done
- `panel-menu.png` - 3-dot dropdown open
- `panel-export.png` - in-panel Export-as-markdown sheet
- `panel-mobile.png` - the panel on a phone (touch layout)
- `panel-populated-light.png` - populated plan (light theme)

Still to record from the explainer artifact (screen-record + optionally add the
ElevenLabs voiceover, then trim to a loop):

- `wingman-demo.gif` - top hero loop (tasks checking off + sync)
- `connect-3steps.gif` - the 3-step connect sequence

The animated source for the GIFs is the explainer artifact (published from
`docs/launch/wingman-explainer.html`). The PNGs above were rendered from the real
panel code (`src/wingman/ui/static/`) with sample plan data, so they match the
live panel pixel for pixel.
