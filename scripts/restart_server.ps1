# Stop any running RescueRoute server and start a fresh one with logs.
# Also stops any stray facility-builder process.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'app\.py|build_facilities' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

$root = Split-Path -Parent $PSScriptRoot
$pythonBin = if (Test-Path "G:\Python314\python.exe") { "G:\Python314\python.exe" } else { "python" }
Start-Process -FilePath $pythonBin -ArgumentList "-u", "app.py" `
  -WorkingDirectory $root `
  -RedirectStandardOutput (Join-Path $root "data\server.log") `
  -RedirectStandardError (Join-Path $root "data\server.err.log") `
  -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 700
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:5000/api/status" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch { }
}
if ($ok) {
  Write-Output "server ready on http://127.0.0.1:5000"
} else {
  Write-Output "server NOT ready"
  Get-Content (Join-Path $root "data\server.err.log") -Tail 20
  exit 1
}
