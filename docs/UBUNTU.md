# TabPilot on headless Ubuntu

A logged-in Chrome running on a server, up around the clock, driven from your
laptop — with no port exposed to the network.

This is the setup TabPilot was built for. Everything here also works on a
desktop Linux session; the parts that are specific to a headless server are
marked as such.

---

## What gets installed

```bash
git clone https://github.com/lephuochung/tabpilot-mcp && cd tabpilot-mcp
bash deploy/ubuntu/install.sh
```

One command, idempotent, safe to re-run — including over SSH, where it skips the
VNC password prompt rather than hanging on a prompt nobody can answer. It
installs:

| Piece | Why |
|:--|:--|
| `xvfb` | A virtual display. Chrome in true headless mode behaves differently enough from a real browser that some sites notice; a framebuffer gets you a normal Chrome. |
| `openbox` | A window manager, so windows get sized and stacked rather than appearing at 0×0. |
| `x11vnc` | Lets you sign in to sites by hand, once. Optional — skip it with `--no-vnc`. |
| `x11-utils` | `xdpyinfo`, which the units use to wait for the display instead of sleeping a fixed number of seconds. |
| `fonts-noto-core`, `fonts-noto-cjk`, `fonts-liberation`, `fontconfig` | **Read the font section below before skipping these.** |
| `google-chrome-stable` | Installed from Google's `.deb` if no Chrome or Chromium is present. |
| Four systemd user units | Chrome that comes back after a crash, a logout, and a reboot. |

Then:

```bash
tabpilot doctor
```

---

## The font problem

On a minimal server image Chrome renders text it has no font for as **empty
boxes**, and reports no error at all. Nothing fails. Nothing warns. The process
producing the screenshots believes it succeeded.

The cost is that you find out later, when the evidence you were collecting turns
out to be unreadable — and there is no way to re-take a screenshot of a page
state that has since moved on.

`install.sh` installs Noto and Liberation. `doctor` checks Vietnamese and CJK
coverage specifically, because those are the scripts a Latin-only font set
silently drops:

```
Fonts (Linux)
  ✓ fonts: 412 fonts, including Vietnamese and CJK coverage
```

If you provisioned the host some other way:

```bash
sudo apt install -y fontconfig fonts-liberation fonts-noto-core fonts-noto-cjk
fc-cache -f
```

---

## The managed stack

Four user units, with an ordering chain and `Restart=always`:

```
tabpilot-xvfb.service   →   tabpilot-wm.service   →   tabpilot-chrome.service
                        →   tabpilot-vnc.service
```

```bash
tabpilot up                   # start (systemd pulls in the dependencies)
tabpilot up --no-vnc          # Chrome and its display, without VNC
tabpilot up --restart         # after editing the env file
tabpilot down                 # stop, leaves first
tabpilot status               # what is running
tabpilot logs chrome -f       # follow; also: xvfb, wm, vnc
tabpilot uninstall-stack      # remove the units, keep the Chrome profile
```

Settings live in `~/.config/tabpilot/stack.env`. Edit it, then
`tabpilot up --restart`.

### Why units rather than a startup script

The obvious approach is a shell script that `pkill`s the old processes and
backgrounds new ones with `&`. It works, until:

- **the host reboots** and nothing comes back;
- **Chrome crashes** and stays dead, because nothing is watching it;
- **you log out** and systemd tears down your whole session — which is why
  `install.sh` enables `loginctl enable-linger`;
- **something breaks** and the log is in `/tmp`, rotated away before you looked.

Units fix all four, and `journalctl --user -u tabpilot-chrome` keeps the history.

### Chrome flags, and why each is there

```
--remote-debugging-address=127.0.0.1   never expose CDP to the network
--user-data-dir=<profile>              a real profile, outside the default one
--disable-dev-shm-usage                containers ship a 64 MB /dev/shm; tabs OOM
--password-store=basic                 without it Chrome blocks on a keyring
                                       that no headless server is running
--disable-gpu                          nothing to accelerate onto
--no-first-run --no-default-browser-check
```

`--password-store=basic` is the one that wastes an afternoon. Chrome starts,
appears to hang, and the log says nothing useful: it is waiting on
gnome-keyring, which is not there.

### Sharing the host with something else

Chrome is happy to use every spare gigabyte. If the host also runs something
that must not be starved, cap it at install time:

```bash
tabpilot install-stack --memory-high 4G
```

That is `MemoryHigh`, not `MemoryMax`: above the threshold the kernel applies
reclaim pressure instead of inviting the OOM killer, so a runaway Chrome slows
down rather than dying in the middle of a workflow. Leave it unset for no limit.

### `--no-sandbox`

`--no-sandbox` is deliberately **not** in the flag list above. If you run the stack as
root, Chrome's sandbox refuses to start and you will be tempted to add it —
adding it removes a real security boundary around a browser holding your live
sessions. Run the stack as an unprivileged user instead. `doctor` warns when you
are root.

---

## Signing in

The Chrome profile starts empty. Sign in to your sites once, by hand, through a
tunnelled VNC session:

```bash
# on your laptop
ssh -N -L 5901:127.0.0.1:5901 you@your-server
```

Then point any VNC client at `localhost:5901`. On macOS, Finder → **Cmd+K** →
`vnc://localhost:5901`.

Sign in to what you need, then just close the viewer. Chrome keeps running, and
the sessions persist in the profile.

If the install ran over SSH it will have skipped VNC, since there was no
terminal to set a password on. Add it on the server when you need it:

```bash
mkdir -p ~/.vnc && x11vnc -storepasswd ~/.vnc/passwd
tabpilot install-stack && tabpilot up
```

### Why VNC is locked down by default

`x11vnc -nopw -forever` on all interfaces is a common recipe and a bad idea
here: it is an **unauthenticated remote desktop onto a browser that is logged
into your accounts**. Anyone who can reach port 5901 can use those accounts, and
no password is required.

A private network such as Tailscale covers most of it, but a host usually also
has a LAN address, and one misconfigured firewall rule is all it takes.

So the default is `-localhost -rfbauth ~/.vnc/passwd`, reached through the SSH
tunnel above. `install.sh` prompts for the password on first run. If you want the
old behaviour, ask for it explicitly:

```bash
bash deploy/ubuntu/install.sh --vnc-insecure
```

`doctor` flags an exposed VNC as a failure, not a warning.

---

## Connecting your MCP client

**Recommended: stdio over SSH.** MCP speaks over stdio and SSH is a pipe, so
this needs nothing else — and no port is opened anywhere.

```json
{
  "mcpServers": {
    "tabpilot": {
      "command": "ssh",
      "args": ["you@your-server", "TABPILOT_REMOTE=1 ~/.local/bin/tabpilot serve"]
    }
  }
}
```

`TABPILOT_REMOTE=1` makes `screenshot` return the image inline. Without it the
file is saved on the server, where your client cannot see it.

Use an SSH key with no passphrase prompt, and make sure the command path is
absolute — a non-interactive SSH session usually does not have
`~/.local/bin` on `PATH`.

**Alternative: an SSH tunnel to the CDP port.**

```bash
ssh -N -L 9222:127.0.0.1:9222 you@your-server
```

Then run TabPilot locally against `127.0.0.1:9222`. This also works, with one
wrinkle: screenshots are saved on your laptop while the tab lives on the server,
so `full_page` captures are fine but anything referring to local paths gets
confusing. Prefer the first option.

**What not to do:** `--remote-debugging-address=0.0.0.0`. The DevTools protocol
has no authentication whatsoever. Anyone who can route to that port can read
every cookie in the profile and act as you on every site you signed into.

---

## Diagnosing

`tabpilot doctor` is the first thing to run whenever anything is off. On Linux it
checks, and tells you the command to fix, each of:

- Chrome present, and which build
- the CDP port answering, and the browser version behind it
- which backend got selected, and a live `1 + 1` round-trip through it
- `DISPLAY`, Xvfb on the expected display, x11vnc
- **VNC exposure** — `-nopw` without `-localhost` is a failure
- font coverage, including Vietnamese and CJK
- `/dev/shm` size
- whether you are root
- the Chrome profile path, and whether it sits in a temp directory that will be
  cleaned
- each unit's state, with the `journalctl` line to read it

---

## Troubleshooting

**`cdp 127.0.0.1:9222: No Chrome answering`**
`tabpilot status`. If `tabpilot-chrome` is not active, `tabpilot logs chrome -n 100`.

**Chrome restarts in a loop**
`tabpilot logs chrome`. Usually `/dev/shm` (see `doctor`), a profile directory
that is not writable, or a snap-confined Chromium that cannot reach a
`--user-data-dir` outside `$HOME`. Prefer the Google `.deb` build.

**`Tab ... exposes no debugger URL`**
Something attached DevTools to that tab. A target with DevTools attached loses
its `webSocketDebuggerUrl`. Close the DevTools panel.

**Screenshots are blank or full of empty boxes**
Blank: Xvfb is not running, or Chrome is on a different `DISPLAY` — check
`doctor`. Empty boxes: fonts. See above.

**Logged out of everything after a reboot**
The profile was in a temp directory. `doctor` warns about this. Move it:

```bash
# ~/.config/tabpilot/stack.env
TABPILOT_USER_DATA_DIR=/home/you/tabpilot-chrome
```

then `tabpilot up --restart`.

**Everything dies when I log out**
Lingering is not enabled:

```bash
sudo loginctl enable-linger "$USER"
```

**`tabpilot: command not found` over SSH**
A non-interactive SSH session has a minimal `PATH`. Use the absolute path in
your MCP config: `~/.local/bin/tabpilot serve`.

---

## Running the tests on the server

```bash
.venv/bin/pytest            # unit tests, no browser needed
.venv/bin/pytest -m live    # end-to-end against the running Chrome
```

The live suite serves its own fixture page over loopback and drives it for real:
trusted clicks, form filling, matrix questions, screenshots. It is skipped rather
than failed when no CDP port answers, so it is safe to run anywhere.
