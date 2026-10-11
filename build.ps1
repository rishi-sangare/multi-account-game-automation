# Build the deliverable: dist\ProTankiCommander\ (Commander + mock client + config + VPS scripts)
# and dist\ProTankiCommander-<version>.zip.  Runs on a fresh Windows 10/11/Server 2019+ box - no Python needed.
#
#   powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
$version = (& $py -c "import commander; print(commander.__version__)").Trim()
$out = "dist\ProTankiCommander"

& $py tools\make_icon.py

# 1. Commander - one folder (fast start, far fewer antivirus false positives than --onefile). No UPX.
& $py -m PyInstaller --noconfirm --clean --windowed --noupx `
    --name ProTankiCommander --icon assets\icon.ico `
    --distpath dist --workpath build\pyi `
    --exclude-module tkinter --exclude-module unittest --exclude-module pydoc --exclude-module cv2 `
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtPdf --exclude-module PySide6.QtSvg `
    run.py

# Qt pieces a widgets app never loads: software OpenGL, QML/Quick, PDF image plugin, translations.
$int = "$out\_internal\PySide6"
Remove-Item -Force -ErrorAction SilentlyContinue "$int\opengl32sw.dll", "$int\Qt6Quick.dll", "$int\Qt6Qml.dll",
    "$int\Qt6QmlModels.dll", "$int\Qt6QmlMeta.dll", "$int\Qt6QmlWorkerScript.dll", "$int\Qt6Pdf.dll",
    "$int\Qt6VirtualKeyboard.dll", "$int\plugins\imageformats\qpdf.dll"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$int\translations", "$int\plugins\platforminputcontexts"

# 2. Mock client - single small exe next to the Commander.
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --noupx `
    --name ProTankiMock --icon assets\icon.ico `
    --distpath $out --workpath build\pyi-mock `
    --exclude-module tkinter --exclude-module numpy --exclude-module PySide6 --exclude-module cv2 `
    mock\mock_protanki.py

# 3. Install cloner (stdlib only) so a fresh VPS needs no Python for setup either.
& $py -m PyInstaller --noconfirm --clean --onefile --console --noupx `
    --name CloneInstall --distpath "$out\tools" --workpath build\pyi-clone `
    --exclude-module tkinter --exclude-module numpy --exclude-module PySide6 --exclude-module cv2 `
    tools\clone_install.py

# 4. Data next to the exe (editable by the user).
Copy-Item -Recurse -Force config "$out\config"
Copy-Item -Recurse -Force vps "$out\vps"
Copy-Item -Force README.md, SETUP.md "$out\"
New-Item -ItemType Directory -Force "$out\tools" | Out-Null
Copy-Item -Force tools\clone_install.py, tools\dump_windows.py, tools\spike_input.py, tools\spike_capture.py "$out\tools\"
Remove-Item -Recurse -Force "$out\logs" -ErrorAction SilentlyContinue

# 5. Zip
$zip = "dist\ProTankiCommander-$version.zip"
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$out\*" -DestinationPath $zip
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "`nBuilt $out  ->  $zip ($size MB)" -ForegroundColor Green
