$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$root=$PSScriptRoot
$mutex=New-Object Threading.Mutex($false, 'Local\VoiceBridge.Setup.0.4')
$held=$false
$locks=New-Object 'System.Collections.Generic.List[System.IO.FileStream]'
try {
    try { $held=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held=$true }
    if (-not $held) { throw 'Другой установщик или обновление Voice Bridge уже запущены.' }
    $ini=Join-Path $root 'current.ini'
    $entries=@([IO.File]::ReadAllLines($ini) | Where-Object { $_.StartsWith('python=') })
    if ($entries.Count -ne 1) { throw 'Некорректный current.ini: не найдено одно активное окружение.' }
    $current=[IO.Path]::GetFullPath($entries[0].Substring(7).Trim())
    $prefix=[IO.Path]::GetFullPath((Join-Path $root 'versions'))+'\'
    if (-not $current.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Недопустимый путь окружения.' }
    $python=Join-Path ([IO.Path]::GetDirectoryName($current)) 'python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python активной версии отсутствует.' }
    if ([Windows.Forms.MessageBox]::Show('Переключить приложение на предыдущую установленную версию? Настройки сохранятся.','Voice Bridge','YesNo') -ne 'Yes') { exit 0 }
    foreach ($name in @('desktop','engine')) {
        $p=Join-Path $env:LOCALAPPDATA ('VoiceBridge\'+$name+'.lock')
        $f=$null
        try {
            $f=[IO.File]::Open($p,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
            $f.Lock(0,1);$locks.Add($f);$f=$null
        } catch { throw 'Сначала закройте Voice Bridge через значок в трее.' }
        finally { if ($f) { $f.Dispose() } }
    }
    $argv=@('-I','-X','utf8',(Join-Path $root 'activation.py'),'rollback','--root',$root)
    $output=& $python @argv 2>&1
    if ($LASTEXITCODE -ne 0) { throw ('Откат не выполнен: '+($output -join "`n")) }
    [Windows.Forms.MessageBox]::Show('Предыдущая версия активирована.','Voice Bridge') | Out-Null
} catch {
    [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Voice Bridge') | Out-Null
    exit 1
} finally {
    foreach ($f in $locks) { $f.Dispose() }
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
