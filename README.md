# Linux Emulator Shuffler

> **AI Disclaimer**
> This application and its documentation were developed collaboratively with AI assistance. Code, window-management scripting logic, and filtering implementations have been tested for stability, but behavior may vary across different Linux distributions, window managers, and display server environments (X11 vs. Wayland). Please review and test scripts locally before using them in production streaming or live event environments.

A PySide6 GUI utility for Linux (KDE Wayland & X11) designed to randomly shuffle focus between running emulator processes at configurable intervals. Built for multi-game speedruns, challenge runs, and multi-streamer setups.

## Key Features

- **Multi-Backend Support:** Native window management handling via `wmctrl` (X11 / Xwayland) and KWin DBus scripting (KDE Wayland).
- **Background Freeze (SIGSTOP):** Unfocused emulators are suspended, not just minimized, so games keep their state instead of playing on in the background. This covers emulators with no "pause when unfocused" option of their own, such as Cemu, and works through Flatpak wrapper trees (`bwrap` -> `Cemu-wrapper` -> `cemu`). Every exit path thaws what it froze.
- **Window-Aware Filtering:** Detects running emulators while automatically filtering out generic shell wrappers (`bash`), sandboxes (`bwrap`), and launcher scripts. Emulators are identified by process name and binary rather than by their full command line, so a process that merely mentions an emulator in an argument (a KDE `kioworker` carrying the requesting app's socket name, or a ROM path) is not mistaken for one. Detection does not rely on `wmctrl` alone, which reports only X11/Xwayland windows: a process is also recognized as an emulator if it links a display-client library, which is what makes native Wayland clients such as Flatpak Cemu visible.
- **Specialized Emulator Handling:** Includes fallback title/class matching for emulators like Rosalie's Mupen GUI (RMG) that spawn isolated process trees or report PID 0 under Xwayland/KWin.
- **Global Hotkeys:** Press `Delete` anywhere to instantly drop the active emulator process from rotation and switch targets immediately.
- **Debounce Lock Protection:** Built-in key-repeat debounce protection prevents rapid signal spam from clearing your active pool on a single keypress.
- **Theme-Aware UI Highlighting:** Dynamically highlights active rotation targets in soft green (`#d4edda`) while maintaining contrast and readability across both light and dark desktop themes.

## Supported Emulators

The shuffler automatically scans for common retro and modern emulators, AppImages, and Flatpak installations:

| Platform                           | Emulators                                                         |
| ---------------------------------- | ----------------------------------------------------------------- |
| Nintendo 64                        | `rmg`, `mupen64plus`, `mupen64plus-qt`, `mupen64plus-gui`, `ares` |
| GameCube / Wii / Wii U / Switch    | `dolphin-emu`, `cemu`, `yuzu`, `suyu`, `ryujinx`, `sudachi`       |
| PlayStation (1 / 2 / 3 / Portable) | `duckstation`, `pcsx2`, `rpcs3`, `ppsspp`, `epsxe`, `mednafen`    |
| Game Boy / GBA / DS / 3DS          | `mgba`, `vbam`, `melonds`, `desmume`, `citra`, `azahar`           |
| Multi-System / Frontends           | `retroarch`, `bizhawk`, `pico8`, `flycast`, `blastem`             |

## Prerequisites

### System Dependencies

- **Python:** 3.10 or higher
- **X11 / Xwayland Utility:** `wmctrl` (required for X11/Xwayland window focusing and visibility toggles)

On Fedora / RHEL:

```bash
sudo dnf install wmctrl
```

On Ubuntu / Debian:

```bash
sudo apt install wmctrl
```

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/linux-emulator-shuffler.git
   cd linux-emulator-shuffler
   ```
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install Python dependencies:
   ```bash
   pip install PySide6 psutil pynput
   ```

## Usage

1. Launch your desired emulators and load your game states.
2. Launch the application:
   ```bash
   python main.py
   ```
3. Click **Refresh Emulator List** if emulators were opened after starting the shuffler.
4. Highlight and select the active emulator processes in the table.
5. Set your minimum and maximum shuffle interval (in seconds).
6. Click **Start Shuffler**.

## Controls & Hotkeys

| Control                   | Action                                                                                                                   |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **Start / Stop Shuffler** | Toggles the automatic timer and locks/unlocks process selection.                                                         |
| **Delete Key (Global)**   | Immediately removes the active emulator process from rotation and jumps to the next target without stopping the session. |

## Troubleshooting

- **Brief audio buzz when a game is shuffled away:** Suspending a process can leave the last audio buffer looping for a few milliseconds before PipeWire drains it. This is cosmetic and does not affect emulation state.
- **An emulator looks hung after a crash:** If the shuffler is killed with `SIGKILL` it cannot thaw its targets. Run `kill -CONT <pid>` to resume one by hand. Normal exits, including closing the window and stopping the shuffler, always resume every process.
- **An unrelated process appears in the list:** Matching is on the process's own name and binary, with its arguments consulted only for interpreters (`mono`, `python`, shells) that are named after themselves rather than after what they run. If something unrelated still appears, add its binary name to `excluded_binaries`, which matches exactly. KDE's `dolphin` file manager is excluded there by default so it is not confused with the Dolphin emulator (`dolphin-emu`).
- **A Flatpak emulator does not appear in the list:** The list shows the process that actually links a display library, which for a Flatpak is the emulator itself rather than its `bwrap` or `*-wrapper` parents. If nothing appears, confirm the emulator's window is open before clicking **Refresh Emulator List**, and check that its binary name matches one of the keywords in `emulator_keywords`.
- **RMG or Flatpak emulators not focusing on Wayland:** Ensure your desktop environment allows global keyboard hooks for `pynput` and DBus scripting calls to KWin (`org.kde.KWin`).
- **Global hotkeys not responding:** Verify your terminal or desktop session has appropriate permissions to capture global keypress events.
