# Leave the VPS without locking it.
#
# Closing Remote Desktop with the X locks the session: the desktop stops rendering and pixel-based
# automation goes blind. This moves your session to the console instead, so it stays unlocked and
# keeps drawing. Run as Administrator (the .bat next to this file does that for you).

$ErrorActionPreference = 'Stop'
$me = $env:USERNAME
$lines = (query session) 2>$null
$id = $null
foreach ($line in $lines) {
    # columns: SESSIONNAME USERNAME ID STATE ...   (the current session is marked with '>')
    if ($line -match ('^\>?\s*(\S+)?\s+' + [regex]::Escape($me) + '\s+(\d+)\s+Active')) {
        $id = $Matches[2]
        break
    }
}
if (-not $id) {
    Write-Host "Could not find an active session for $me." -ForegroundColor Red
    query session
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "Moving session $id to the console - your RDP window will close, the desktop keeps running."
& "$env:windir\System32\tscon.exe" $id /dest:console

