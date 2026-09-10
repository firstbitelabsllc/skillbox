import { pathToFileURL, fileURLToPath } from "node:url";
import { spawn, execFileSync } from "node:child_process";
import { mkdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(path.resolve(process.env.PLAYWRIGHT_MODULE)).href
    : "playwright"
);
const root = fileURLToPath(new URL("../", import.meta.url));
execFileSync("ffmpeg", ["-version"], { stdio: "ignore" });
const output = path.join(root, "docs");
await mkdir(output, { recursive: true });
const server = spawn(
  "ttyd",
  [
    "-p",
    "8855",
    "-i",
    "127.0.0.1",
    "-O",
    "-o",
    "-W",
    "-w",
    root,
    "-t",
    "fontSize=18",
    "-t",
    "screenReaderMode=true",
    "-t",
    "fontFamily=Menlo",
    "-t",
    'theme={"background":"#f4f2eb","foreground":"#212920","cursor":"#b6532a"}',
    "claude",
    "--safe-mode",
    "--setting-sources", "",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
    "--model", "sonnet",
    "--effort", "low",
    "--tools", "Bash,Read",
    "--allowedTools", "Bash(python3 examples/demo.py)",
  ],
  {
    env: {
      ...process.env,
      BASH_SILENCE_DEPRECATION_WARNING: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
let logs = "";
server.stderr.on("data", (chunk) => (logs += chunk));
let browser;
try {
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(Error(logs || "ttyd startup timeout")),
      10000,
    );
    server.stderr.on("data", () => {
      if (logs.includes("Listening on port")) {
        clearTimeout(timeout);
        resolve();
      }
    });
    server.once("exit", (code) => {
      clearTimeout(timeout);
      reject(Error("ttyd exited " + code + " " + logs));
    });
  });
  browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: output, size: { width: 1440, height: 900 } },
  });
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:8855");
  await page.locator(".xterm-helper-textarea").waitFor({ state: "attached" });
  await page.addStyleTag({
    content:
      "body{background:#e6e4dd!important;margin:0!important;padding:0!important}#terminal-container{position:fixed!important;inset:60px!important;width:auto!important;height:auto!important;border-radius:12px;overflow:hidden;padding:0!important;background:#f4f2eb;box-sizing:border-box!important;box-shadow:0 0 0 20px #f4f2eb}.xterm{height:100%!important;padding:0!important}",
  });
  // ttyd fits xterm before the inset frame lands. Resize twice after the frame
  // is present so the terminal recalculates its full visible grid.
  await page.setViewportSize({ width: 1439, height: 900 });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(800);
  await page.waitForFunction(
    () => document.body.textContent.includes("Claude Code"),
    null,
    { timeout: 15000 },
  );
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type(
    "Show me how Skillbox links one skill into two temporary tool folders. Run python3 examples/demo.py and summarize the observed result in two bullets, 35 words total.",
    { delay: 25 },
  );
  await page.keyboard.press("Enter");
  // Claude's xterm canvas does not expose the visible completion footer to
  // DOM text queries. This short settle window follows the live tool result;
  // it captures the native rendered transcript without adding a long idle tail.
  await page.waitForTimeout(15000);
  // The model may leave a suggested follow-up in the composer. Clear only that
  // editable input so the finished screen shows the completed interaction.
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1000);
  // Replace the auto-mode suggestion with a visually blank, unsent composer.
  await page.keyboard.type(" ");
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(output, "skillbox-agent-demo.png") });
  await page.waitForTimeout(800);
  const video = page.video();
  await context.close();
  await video.saveAs(path.join(output, "skillbox-agent-demo.webm"));
  await video.delete();
  for (const name of ["skillbox-agent-demo.png", "skillbox-agent-demo.webm"])
    if ((await stat(path.join(output, name))).size < 1000)
      throw Error("Capture missing: " + name);
  await writeFile(
    path.join(output, "capture-result.json"),
    JSON.stringify(
      {
        commands: [
          "Claude Code receives the native interactive demo prompt",
          "Claude Code runs python3 examples/demo.py",
        ],
        browser: browser.version(),
        recordedAt: new Date().toISOString(),
      },
      null,
      2,
    ) + "\n",
  );
  execFileSync(
    "ffmpeg",
    [
      "-y",
      "-i",
      path.join(output, "skillbox-agent-demo.webm"),
      "-an",
      "-c:v",
      "libx264",
      "-pix_fmt",
      "yuv420p",
      "-movflags",
      "+faststart",
      path.join(output, "skillbox-agent-demo.mp4"),
    ],
    { stdio: "ignore" },
  );
  const cover = await browser.newPage({
    viewport: { width: 1280, height: 640 },
  });
  await cover.goto(pathToFileURL(path.join(output, "cover.html")).href);
  await cover.evaluate(() => document.fonts.ready);
  await cover.screenshot({ path: path.join(output, "cover.png") });
  console.log("Verified native Claude Code screenshot, video, and cover exist.");
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
}
