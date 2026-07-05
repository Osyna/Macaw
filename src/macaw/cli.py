from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time

EPILOG = """\
examples:
  macaw                      start the background service (system tray)
  macaw trigger              toggle recording — bind this to a hotkey
  macaw status               is the service running? which model is active?
  macaw stop                 stop the running service
  macaw settings             open the Settings window
  macaw models               open the Model Manager
  macaw setup                pick and download a model (first-run friendly)
  macaw list                 show every model and which are downloaded
  macaw download large-v3    download a model's weights
  macaw devices              list microphones and their indices
  macaw config edit          open the config file in $EDITOR
  macaw repl                 push-to-talk transcription in the terminal

hotkey:
  Bind `macaw trigger` to a key to start/stop dictation, e.g.
    Hyprland:  bind = SUPER, R, exec, macaw trigger
    Sway:      bindsym $mod+r exec macaw trigger

Config lives at ~/.config/macaw/config.yaml (grouped and commented).
"""


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("macaw")
    except PackageNotFoundError:
        return "0.0.0+dev"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # back-compat flags used by older launchers / desktop files / keybinds
    if "--settings" in argv:
        return _open("SETTINGS")
    if "--models" in argv:
        return _open("MODELS")
    if "--trigger" in argv:
        return cmd_trigger(None)
    args = _parser().parse_args(argv)
    return args._func(args) or 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="macaw",
        description="Macaw — fast, private, local speech-to-text for Linux.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-v", "--version", action="version", version=f"macaw {_version()}")
    sub = p.add_subparsers(title="commands", metavar="<command>")

    # -- service control --
    r = sub.add_parser("run", help="start the tray service (default with no command)")
    r.set_defaults(_func=cmd_run)

    tg = sub.add_parser("trigger", help="toggle recording (bind to a hotkey)")
    tg.set_defaults(_func=cmd_trigger)

    st = sub.add_parser("stop", help="stop the running service")
    st.set_defaults(_func=cmd_stop)

    stat = sub.add_parser("status", help="show whether the service is running")
    stat.set_defaults(_func=cmd_status)

    s = sub.add_parser("settings", help="open Settings in the running service")
    s.set_defaults(_func=lambda a: _open("SETTINGS"))

    m = sub.add_parser("models", help="open the Model Manager in the running service")
    m.set_defaults(_func=lambda a: _open("MODELS"))

    # -- models --
    ls = sub.add_parser("list", help="list speech-to-text models and their status")
    ls.set_defaults(_func=cmd_list)

    dl = sub.add_parser("download", help="download a model's weights")
    dl.add_argument("model", nargs="?", help="model id (default: the active model)")
    dl.set_defaults(_func=cmd_download)

    su = sub.add_parser("setup", help="interactive first-run setup (pick + download)")
    su.set_defaults(_func=cmd_setup)

    # -- audio / config --
    d = sub.add_parser("devices", help="list input microphones and their indices")
    d.set_defaults(_func=cmd_devices)

    c = sub.add_parser("config", help="show, locate, or edit the config file")
    c.add_argument(
        "action",
        nargs="?",
        choices=["show", "path", "edit"],
        default="show",
        help="show (default) | path | edit",
    )
    c.set_defaults(_func=cmd_config)

    rp = sub.add_parser("repl", help="push-to-talk transcription in the terminal")
    rp.set_defaults(_func=cmd_repl)

    p.set_defaults(_func=cmd_run)  # no subcommand → run the service
    return p


# ── service / IPC ────────────────────────────────────────────────────


def cmd_run(_args: object) -> int:
    # If a service is already running (e.g. launched from the app menu while the
    # systemd unit is active), open its Settings instead of starting a 2nd tray.
    from macaw.trigger import send_command

    if send_command("SETTINGS", timeout_ms=500) is not None:
        print("Macaw is already running — opened Settings.")
        return 0

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    from macaw.service import MacawService

    MacawService().run()
    return 0


def _open(command: str) -> int:
    from macaw.trigger import send_command

    if send_command(command) is None:
        print("Macaw is not running. Start it with:  macaw")
        return 1
    return 0


def _running() -> bool:
    """True if a service is listening on the IPC socket."""
    from macaw.trigger import send_command

    return send_command("PING", timeout_ms=800) is not None


def cmd_trigger(_args: object) -> int:
    """Toggle recording on the running service (the hotkey action)."""
    from macaw.trigger import send_command

    if send_command("TOGGLE") is None:
        print("Macaw is not running. Start it with:  macaw")
        return 1
    return 0


def cmd_stop(_args: object) -> int:
    from macaw.trigger import send_command

    reply = send_command("STOP")
    if reply is None:
        print("Macaw is not running.")
        return 1
    if reply != "OK":
        # An older service build that predates the STOP command.
        print("This Macaw service is too old to stop over IPC. Restart it with:")
        print("  systemctl --user restart macaw")
        return 1
    print("Macaw stopped.")
    return 0


def cmd_status(_args: object) -> int:
    from rich.console import Console

    from macaw.config import Config, config_path

    console = Console()
    running = _running()
    cfg = Config.load()
    if running:
        console.print("[green]●[/] Macaw is [green]running[/]")
    else:
        console.print(
            "[dim]○[/] Macaw is [red]not running[/]  ([dim]start it with:[/] macaw)"
        )
    console.print(f"  [dim]model  [/] {cfg.model}")
    console.print(f"  [dim]theme  [/] {cfg.theme}")
    console.print(f"  [dim]config [/] {config_path()}")
    console.print(f"  [dim]version[/] {_version()}")
    return 0 if running else 3


# ── models ───────────────────────────────────────────────────────────


def _model_ids() -> list[str]:
    from macaw.stt import list_models

    return [m.id for m in list_models()]


def _status(backend, info, active: str) -> tuple[str, str]:
    """(label, rich-color) for a model's current state."""
    if info.id == active:
        return "active", "green"
    if info.extra and not backend.available():
        return f"needs macaw[{info.extra}]", "yellow"
    if backend.is_ready():
        return "ready", "cyan"
    if not backend.hf_repos():
        return "on first use", "dim"
    return "not downloaded", "dim"


def cmd_list(_args: object) -> int:
    from rich.console import Console
    from rich.table import Table

    from macaw.config import Config
    from macaw.stt import create_backend, list_models

    active = Config.load().model
    table = Table(title="macaw models", title_style="bold")
    for col in ("id", "name", "backend", "size", "langs", "status"):
        table.add_column(col)
    for info in list_models():
        label, color = _status(create_backend(info.id), info, active)
        table.add_row(
            info.id,
            info.label,
            info.backend,
            info.size,
            info.languages,
            f"[{color}]{label}[/{color}]",
        )
    Console().print(table)
    print("\nDownload one with:  macaw download <id>")
    return 0


def cmd_devices(_args: object) -> int:
    from macaw.audio.capture import AudioCapture
    from macaw.config import Config

    current = Config.load().device_index
    mark = "→" if current is None else " "
    print(f"{mark} default  System Default")
    for i, dev in enumerate(AudioCapture.list_devices()):
        if dev["max_input_channels"] > 0:
            mark = "→" if current == i else " "
            print(f"{mark} {i:>7}  {dev['name']}")
    print("\nSet one in config.yaml (device_index) or the Settings window.")
    return 0


def cmd_download(args: object) -> int:
    from rich.console import Console
    from rich.progress import BarColumn, Progress, TextColumn

    from macaw.config import Config
    from macaw.stt import create_backend, get_model_info

    console = Console()
    model_id = getattr(args, "model", None) or Config.load().model
    if model_id not in _model_ids():
        console.print(f"[red]Unknown model:[/] {model_id}")
        console.print("Run [bold]macaw list[/] to see valid ids.")
        return 1

    info = get_model_info(model_id)
    backend = create_backend(model_id)
    if info.extra and not backend.available():
        console.print(
            f"[yellow]{info.label}[/] needs its backend installed first:\n"
            f"  pip install 'macaw[{info.extra}]'"
        )
        return 1
    if not backend.hf_repos():
        console.print(
            f"[cyan]{info.label}[/] downloads automatically the first time you use it."
        )
        return 0
    if backend.is_ready():
        console.print(f"[green]{info.label}[/] is already downloaded.")
        return 0

    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        console=console,
    ) as prog:
        task = prog.add_task(f"Downloading {info.label}", total=100)
        backend.download(progress_callback=lambda pct: prog.update(task, completed=pct))
        prog.update(task, completed=100)
    console.print(f"[green]✓[/] {info.label} downloaded.")
    return 0


def cmd_setup(_args: object) -> int:
    from rich.console import Console
    from rich.prompt import IntPrompt

    from macaw.config import Config
    from macaw.stt import create_backend, list_models

    console = Console()
    console.print("[bold]macaw setup[/] — choose a speech-to-text model.\n")
    cfg = Config.load()
    models = list_models()
    for i, info in enumerate(models):
        label, color = _status(create_backend(info.id), info, cfg.model)
        star = "★" if info.id == cfg.model else " "
        console.print(
            f"  {star} [bold]{i:>2}[/]  {info.label:<26} {info.size:<9} "
            f"[{color}]{label}[/{color}]"
        )
    default = next((i for i, m in enumerate(models) if m.id == cfg.model), 0)
    choice = IntPrompt.ask("\nSelect a model", default=default)
    if not 0 <= choice < len(models):
        console.print("[red]Out of range.[/]")
        return 1
    info = models[choice]

    rc = cmd_download(argparse.Namespace(model=info.id))
    if rc != 0:
        return rc
    cfg.model = info.id
    cfg.save()
    console.print(
        f"\n[green]✓[/] Active model set to [bold]{info.label}[/].\n"
        f"Start macaw with:  [bold]macaw[/]"
    )
    return 0


# ── config ───────────────────────────────────────────────────────────


def cmd_config(args: object) -> int:
    from macaw.config import Config, config_path

    path = config_path()
    action = getattr(args, "action", "show")
    if action == "path":
        print(path)
        return 0
    if not path.exists():
        Config().save(path)  # materialize defaults so there is something to read/edit
    if action == "edit":
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
        return subprocess.call([editor, str(path)])
    print(f"# {path}\n")
    print(path.read_text())
    return 0


# ── push-to-talk REPL ────────────────────────────────────────────────


def cmd_repl(_args: object) -> int:
    import numpy as np
    from rich.console import Console

    from macaw.audio.capture import AudioCapture
    from macaw.audio.transcriber import Transcriber
    from macaw.config import Config

    console = Console()
    cfg = Config.load()
    console.print("[bold green]macaw REPL (push-to-talk)[/]  —  Ctrl+C to quit.\n")

    devices = AudioCapture.list_devices()
    input_devs: list[tuple[int | None, dict]] = [(None, {"name": "System Default"})]
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            input_devs.append((i, dev))
    console.print("[yellow]Input devices:[/]")
    for idx, (_, dev) in enumerate(input_devs):
        console.print(f"  {idx}. {dev['name']}")

    while True:
        try:
            ci = int(input("Select microphone [0]: ").strip() or "0")
            if 0 <= ci < len(input_devs):
                selected = input_devs[ci][0]
                console.print(f"[green]Selected: {input_devs[ci][1]['name']}[/]\n")
                break
        except ValueError:
            pass
        console.print("[red]Invalid.[/]")

    capture = AudioCapture(device=selected)
    transcriber = Transcriber(language=cfg.language)
    console.print("[yellow]Loading model...[/]")
    transcriber.load_model()

    try:
        while True:
            console.print(
                "\n[bold white on blue] Ready [/] Press Enter to START (Ctrl+C to quit)"
            )
            try:
                input()
            except EOFError:
                break
            capture.start()
            capture.last_sound_time = time.time()
            capture.speech_detected = False
            console.print("[bold red] REC [/] Press Enter to STOP")
            try:
                input()
            except EOFError:
                break
            capture.stop()
            chunks = capture.read()
            if chunks:
                audio = np.concatenate(chunks)
                dur = len(audio) / capture.sample_rate
                console.print(f"[dim]Captured {dur:.1f}s.[/]")
                text = transcriber.transcribe(audio, sample_rate=capture.sample_rate)
                console.print(f"[bold cyan]{text}[/]" if text else "[dim]No speech.[/]")
            else:
                console.print("[dim]No audio captured.[/]")
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
        console.print("[bold green]Done.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
