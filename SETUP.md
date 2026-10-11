# Setup guide

## 1. Install on the VPS

1. Copy `ProTankiCommander-x.y.z.zip` to the VPS and extract it, for example to `C:\ProTankiCommander`.
2. Run `ProTankiCommander.exe`.
   - If Windows SmartScreen appears, click **More info → Run anyway**. The exe is not code-signed.
3. No Python, .NET or other runtime is needed.

The first launch shows the demo accounts, which use the built-in mock client. Press **Start all** (F5) to see the tool working before you connect real accounts.

## 2. Prepare one ProTanki client per account

ProTanki's client runs on Adobe AIR, which only allows one running copy per application ID. Give each account its own copy of the client:

```
tools\CloneInstall.exe --src "C:\Program Files (x86)\ProTanki Online" --dst C:\ProTanki --count 8
```

This creates `C:\ProTanki\acc1 … acc8`, each with a unique ID, and prints the exe path for each one. Alternatively, copy the folder by hand and change `<id>` in `META-INF\AIR\application.xml` in each copy.

Log in once in each client and tick "remember me". After that the client opens straight into the lobby.

## 3. Add, edit or remove accounts

- **Add:** click **Add** (Ctrl+N), then fill in:
  - **Name**
  - **Client**:
    - *Launch executable*: pick `C:\ProTanki\accN\ProTanki.exe`. The Commander starts it for you.
    - *Attach to window*: the Commander binds to a client you already opened, matched by window title or by clicking **Bind window**.
  - **Preset**: `protanki` for the real game
  - **Input**: *PostMessage* (background) is the default. If the game ignores it, the tool switches to *SendInput* by itself.
  - **Grid slot**: the window's position in the grid.
- **Edit:** click **⋯ → Edit account**.
- **Remove:** click **⋯ → Remove account**. The client window is left open.
- **Disable temporarily:** edit the account and untick *Enabled*.

All of this is saved in `config\accounts.json`, which you can also edit by hand. Changes are picked up automatically.

## 4. Teach it your buttons (presets)

A preset holds three things:

- **points**: where buttons are, as fractions of the window, so they work at any window size
- **triggers**: how to recognise a screen, by pixel colours or a small template image
- **sequences**: what to click, and in what order

The `protanki` preset ships with placeholder values. Set them on your own client:

1. Bring a client to the screen you want, for example a battle with a free slot and the green **Play** button.
2. Click **Pick** (Ctrl+P). The screen freezes and a magnifier follows the cursor.
3. Click the button and save it:
   - **Point name**: `play_btn`
   - **Trigger**: `join_ready`
   - *Also use this colour as a trigger check*: ticked
4. Do the same for the other triggers:

   | Trigger | What to pick |
   |---|---|
   | `lobby` | Something that only shows in the lobby, such as the top bar |
   | `in_battle` | Something that only shows in battle, such as the HUD |
   | `exit_btn` (point) | The "exit battle" button in the Esc menu |

5. **Drag** instead of clicking to capture a template image. This is more robust when a button has no unique colour.
6. Check the **Live** tab. It shows exactly what the engine sees, with every point drawn on top: green means the colour matches, red means it doesn't.
7. Use **Test** to fire any sequence or point on the selected account.

The **Preset** tab lets you fine-tune numbers, or edit the JSON directly with validation.

## 5. Daily use

| Action | How |
|---|---|
| Start / resume every account | **Start all** or F5 |
| Pause automation (clients stay open) | **Pause all** or F6, or Ctrl+Alt+P anywhere |
| Exit All | Red button, **Ctrl+Alt+X** anywhere, or Telegram `/exitall` |
| Kill all client processes | Ctrl+Alt+K anywhere, or exit mode **Kill** |
| Re-arrange windows | **Tile** or Ctrl+T |

Exit mode (top right, or *Settings*):

- **Leave**: leave the battle and stay in the lobby.
- **Close**: leave the battle, then close the client.
- **Kill**: terminate the clients immediately.

## 6. Keep it running on a VPS (important)

Windows stops drawing the desktop when Remote Desktop is minimised or closed, which leaves any pixel-based tool blind. The Commander shows **CAPTURE: NO RENDER** when that happens.

- **Minimising RDP:** run `vps\fix_rdp_minimized.reg` on **your own PC** (the one you connect from), then reconnect.
- **Leaving the VPS:** don't close RDP with the X. Double-click `vps\Disconnect-Keep-Running.bat` on the VPS instead. Your session moves to the console, stays unlocked and keeps running.
- Keep the VPS display scaling at 100 %, and set the game's graphics to minimum.
- Sizing: plan on about 300–600 MB RAM per client. 8 vCPU and 12–16 GB is comfortable for 10 clients.

## 7. Telegram remote (optional)

1. Create a bot with @BotFather and copy its token.
2. Get your numeric user ID from @userinfobot.
3. In **Settings → Telegram remote**, tick *Enable*, paste the token and your ID, then click **Save & apply**.

Commands: `/status`, `/exitall`, `/kill`, `/pause`, `/resume`, `/screenshot`. Only your user ID is obeyed.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Hotkeys chip says *conflict* | Another app owns that key combination. Change it in *Settings → Hotkeys*. |
| `posted clicks ignored → switching to SendInput` in the log | The game needs real input for that account. This is automatic, but you can set *Input: SendInput* to skip the detection. |
| Joins are not detected | Open the **Live** tab. If a trigger's points are red while the button is visible, re-pick its colour. |
| Clicks land in the wrong place | Check that display scaling is 100 % and click **Tile**. Points are fractions of the client area, so re-picking fixes any layout change. |
| A client keeps relaunching | It is crashing or closing. Check `logs\commander.log` and the game itself. |

Logs are written to `logs\commander.log` (rotated daily, 14 days kept).
