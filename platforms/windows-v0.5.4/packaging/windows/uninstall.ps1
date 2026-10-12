$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$root=Join-Path $env:LOCALAPPDATA 'Programs\VoiceBridge'
try {
    if ([IO.Path]::GetFullPath($PSScriptRoot) -ne [IO.Path]::GetFullPath($root)) { throw 'Неверное расположение деинсталлятора.' }
    foreach ($name in @('desktop','engine')) {
        $p=Join-Path $env:LOCALAPPDATA ('VoiceBridge\'+$name+'.lock')
        if (Test-Path -LiteralPath $p) {
            $f=$null
            try { $f=[IO.File]::Open($p,'Open','ReadWrite','ReadWrite');$f.Lock(0,1);$f.Unlock(0,1) }
            catch { throw 'Сначала закройте Voice Bridge через значок в трее. Другие процессы Python не будут завершены.' }
            finally { if ($f) { $f.Dispose() } }
        }
    }
    if ([Windows.Forms.MessageBox]::Show('Удалить Voice Bridge и его частные окружения? Настройки, журналы, скачанные и внешние модели сохранятся.','Voice Bridge','YesNo') -ne 'Yes') { exit }
    Remove-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name VoiceBridge -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceBridge' -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Programs')) 'Voice Bridge') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Voice Bridge.lnk') -Force -ErrorAction SilentlyContinue
    # Managed models live alongside versions, not inside a disposable runtime.
    # Preserve their directory (including paused downloads); never traverse it.
    Get-ChildItem -LiteralPath $root -Force | Where-Object { $_.Name -ne 'models' } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
    [Windows.Forms.MessageBox]::Show('Приложение удалено. Пользовательские настройки и модели сохранены.','Voice Bridge') | Out-Null
} catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Voice Bridge') | Out-Null;exit 1 }
