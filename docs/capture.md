# Reproduce the demo

The recording is a native Claude Code session in safe mode. It receives a
short interactive prompt to run `python3 examples/demo.py` and summarize the
observed result, then shows the real Skillbox output. The demo creates its own
temporary configuration and folders, verifies both links and a shared edit,
and removes its scratch folder on exit.

The recording uses ttyd, FFmpeg, Node.js 20+, and Playwright. Install ttyd and
FFmpeg on PATH, then run from this checkout:

```sh
capture_tools="$(mktemp -d)"
npm install --prefix "$capture_tools" playwright@1.62.0
"$capture_tools/node_modules/.bin/playwright" install chromium
PLAYWRIGHT_MODULE="$capture_tools/node_modules/playwright/index.mjs" node docs/capture.mjs
```

The script records an isolated terminal on local port 8855. It saves
`skillbox-agent-demo.png`, `skillbox-agent-demo.webm`, `skillbox-agent-demo.mp4`,
`cover.png`, and `capture-result.json` here, then closes its browser and terminal
server. Temporary folder names vary between runs. The expected `SOURCE-NOT-GIT`
line describes this plain-folder fixture.

The box mark was made with OpenAI's built-in image tool. Prompt: an open archive
box holding one folded-corner card, broad flat shapes in ink and muted blue on
ivory, no text, badges, shadows, or gradients. It is an illustration. The terminal
image and video are actual captures.

Space Grotesk is included under the SIL Open Font License in `OFL.txt`.
