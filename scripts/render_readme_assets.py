"""Render README artwork and a terminal animation from real local CLI output.

Requires Pillow (artwork only): python -m pip install Pillow
Run from an installed development checkout: python scripts/render_readme_assets.py
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
BANNER = f"banner-v{VERSION}.png"
DEMO = f"cli-demo-v{VERSION}.gif"
POSTER = f"cli-demo-poster-v{VERSION}.png"
DEMO_PORT = 8765
WHITE = "#f5f5fa"
MUTED = "#a8a9c0"
CYAN = "#91e4ef"
PURPLE = "#b8a0ff"


def font(size: int, *, mono: bool = False, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        ["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
        if mono
        else [
            f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf",
            f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        ]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("Install DejaVu Sans and DejaVu Sans Mono to render the artwork.")


def banner() -> None:
    width, height = 1600, 360
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            glow = math.exp(-(((x - 1250) / 550) ** 2 + ((y - 80) / 310) ** 2))
            pixels[x, y] = (int(12 + 5 * glow), int(13 + 3 * glow), int(17 + 11 * glow))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((1, 1, width - 2, height - 2), radius=16, outline="#2e2e37", width=1)
    d.text((64, 61), "MCP Rig", font=font(78, bold=True), fill=WHITE)
    d.text((68, 166), "Test your MCP servers.", font=font(30), fill=WHITE)
    d.text((68, 218), "YAML suites. Local and remote servers. Ready for CI.",
           font=font(22), fill=MUTED)
    # Three compact examples keep the workflow concrete and easy to scan.
    for x, title, lines in [
        (780, "YAML suite", ["call: add", "args:", "  a: 2", "  b: 3"]),
        (1040, "MCP server", ["stdio · HTTP", "tools/call", "add(2, 3)", "→ 5"]),
        (1300, "Test results", ["✓ 3 passed", "0 failed", "0 errors", "JUnit XML"]),
    ]:
        d.text((x, 88), title, font=font(19), fill=WHITE)
        d.rounded_rectangle((x, 123, x + 220, 267), radius=8,
                            fill="#16161d", outline="#33323e", width=1)
        for i, line in enumerate(lines):
            color = CYAN if line.startswith("✓") else MUTED
            d.text((x + 18, 143 + i * 27), line, font=font(18, mono=True), fill=color)
    for x in (1012, 1272):
        d.line((x, 195, x + 15, 195), fill="#767080", width=1)
        d.line((x + 10, 191, x + 15, 195, x + 10, 199), fill="#767080", width=1)
    mask = Image.new("L", image.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=16, fill=255)
    image = image.convert("RGBA")
    image.putalpha(mask)
    image.save(ASSETS / BANNER, optimize=True)


def interpreter() -> str:
    python = ROOT / ".venv" / "bin" / "python"
    return str(python) if python.exists() else sys.executable


@contextmanager
def remote_fixture():
    """Serve the fixture tools over Streamable HTTP on the demo's fixed port."""
    process = subprocess.Popen(
        [interpreter(), "tests/fixtures/http_server.py", "streamable-http", str(DEMO_PORT)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        if not process.stdout.readline().startswith("PORT "):
            raise RuntimeError(f"The HTTP fixture could not listen on port {DEMO_PORT}.")
        yield f"http://127.0.0.1:{DEMO_PORT}/mcp"
    finally:
        process.terminate()
        process.wait(timeout=10)


def capture(command: list[str]) -> list[str]:
    executable = interpreter()
    env = dict(os.environ)
    env["PATH"] = str(Path(executable).parent) + os.pathsep + env["PATH"]
    process = subprocess.run(
        [executable, "-c", "from mcp_rig.cli import main; raise SystemExit(main())", *command],
        cwd=ROOT, env=env,
        capture_output=True, text=True, check=True,
    )
    output = process.stdout.replace(str(ROOT) + "/", "")
    return output.rstrip().splitlines()


def terminal_frame(command: str, lines: list[str], step: str, cursor: bool = False) -> Image.Image:
    image = Image.new("RGB", (1440, 640), "#090a10")
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((1, 1, 1438, 638), radius=22, outline="#363447", width=2)
    d.rounded_rectangle((2, 2, 1437, 60), radius=20, fill="#181821")
    d.rectangle((2, 35, 1437, 60), fill="#181821")
    for x, color in [(30, "#f07682"), (56, "#dcc18b"), (82, "#93cdb6")]:
        d.ellipse((x, 23, x + 12, 35), fill=color)
    d.text((720, 30), "mcp-rig / terminal", font=font(19, mono=True), fill=MUTED, anchor="mm")
    d.text((40, 91), step, font=font(21, mono=True), fill=PURPLE)
    d.text((40, 145), "$", font=font(22, mono=True), fill=CYAN)
    d.text((68, 145), command, font=font(22, mono=True), fill=WHITE)
    if cursor:
        x = 68 + d.textlength(command, font=font(22, mono=True))
        d.rectangle((x + 2, 148, x + 13, 171), fill=CYAN)
    for i, line in enumerate(lines):
        color = CYAN if line.startswith("✓") or "passed," in line else MUTED
        if line.startswith("Selection:"):
            color = PURPLE
        d.text((40, 197 + 31 * i), line, font=font(22, mono=True), fill=color)
    d.line((40, 576, 1400, 576), fill="#302e40", width=1)
    d.text((40, 597), "YAML  →  MCP SERVER  →  TEST RESULTS", font=font(17, mono=True), fill=MUTED)
    d.text((1400, 597), "MCP RIG", font=font(17, mono=True), fill=WHITE, anchor="ra")
    return image


def terminal_demo() -> None:
    suite_path = ROOT / "suite.yaml"
    if suite_path.exists():
        raise RuntimeError("Move suite.yaml out of the repository root before rendering the demo.")
    init_command = 'mcp-rig init "python tests/fixtures/fixture_server.py" --output suite.yaml'
    try:
        generated = capture(["init", "python tests/fixtures/fixture_server.py", "--output", "suite.yaml"])
        if "wrote 8 cases" not in generated[0]:
            raise RuntimeError("The demo's init step must generate one case per fixture tool.")
    finally:
        suite_path.unlink(missing_ok=True)
    suite = capture(["run", "examples/fixture.yaml"])
    if "3 passed, 0 failed" not in suite[-1]:
        raise RuntimeError("The demo suite must report three passing tests.")
    with remote_fixture() as url:
        remote = capture(["check", url, "--ignore", "param-no-description"])
    scenes = [
        ("01 / GENERATE A SUITE", init_command, generated),
        ("02 / RUN A SUITE", "mcp-rig run examples/fixture.yaml", suite),
        ("03 / CHECK A REMOTE SERVER", f"mcp-rig check {url} --ignore param-no-description", remote),
    ]
    frames, durations = [], []
    for step, command, output in scenes:
        for end in range(0, len(command) + 3, 3):
            frames.append(terminal_frame(command[:min(end, len(command))], [], step, cursor=True))
            durations.append(60)
        frames.append(terminal_frame(command, [], step))
        durations.append(350)
        for line in range(1, len(output) + 1):
            frames.append(terminal_frame(command, output[:line], step))
            durations.append(130)
        durations[-1] = 2700
    # One shared palette avoids flicker between GIF frames.
    palette = frames[-1].quantize(colors=96)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    indexed[0].save(ASSETS / DEMO, save_all=True, append_images=indexed[1:],
                    duration=durations, loop=0, optimize=True, disposal=1)
    frames[-1].save(ASSETS / POSTER, optimize=True)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    banner()
    terminal_demo()
    for name in (BANNER, DEMO, POSTER):
        path = ASSETS / name
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
