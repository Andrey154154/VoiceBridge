$ErrorActionPreference = 'Stop'
try {
    $root = Join-Path $env:LOCALAPPDATA 'Programs\VoiceBridge'
    $ini = Join-Path $root 'current.ini'
    if (-not (Test-Path -LiteralPath $ini)) { throw 'Voice Bridge не установлен. Используйте полный установщик 0.5.4.' }
    $entries = @([IO.File]::ReadAllLines($ini) | Where-Object { $_.StartsWith('python=') })
    if ($entries.Count -ne 1) { throw 'Не удалось определить активное окружение Voice Bridge.' }
    $active = [IO.Path]::GetFullPath($entries[0].Substring(7).Trim())
    $prefix = [IO.Path]::GetFullPath((Join-Path $root 'versions')) + '\'
    if (-not $active.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Недопустимый путь runtime. Используйте полный установщик.' }
    $python = Join-Path ([IO.Path]::GetDirectoryName($active)) 'python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Python Voice Bridge не найден. Используйте полный установщик.' }
    $argv = @('-I','-X','utf8',(Join-Path $PSScriptRoot 'apply_fix.py'),'--root',$root)
    & $python @argv
    exit $LASTEXITCODE
} catch {
    Write-Host ('ОШИБКА: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
