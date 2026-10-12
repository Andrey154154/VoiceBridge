param([Parameter(Mandatory=$true)][string]$Source,
      [Parameter(Mandatory=$true)][ValidateSet('cuda','cpu','delivery')][string]$Profile,
      [Parameter(Mandatory=$true)][string]$Log,
      [Parameter(Mandatory=$true)][string]$Result,
      [Parameter(Mandatory=$true)][string]$CancelFile)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Utf8 = New-Object Text.UTF8Encoding($false)
$Root = Join-Path $env:LOCALAPPDATA 'Programs\VoiceBridge'
$Data = Join-Path $env:LOCALAPPDATA 'VoiceBridge'
$Stage = $null
$Activated = $false
$ReadyForActivation = $false
$mutex = New-Object Threading.Mutex($false, 'Local\VoiceBridge.Setup.0.4')
$held = $false
function Write-Log([string]$Text) {
    [IO.File]::AppendAllText($Log, ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'),$Text) + "`r`n", $Utf8)
}
function Check-Cancel { if (Test-Path -LiteralPath $CancelFile) { throw 'Установка отменена пользователем.' } }
function Assert-Stopped {
    foreach ($name in @('desktop','engine')) {
        $lockFile = Join-Path $Data ($name + '.lock')
        if (Test-Path -LiteralPath $lockFile) {
            $stream = $null
            try {
                $stream = [IO.File]::Open($lockFile,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
                $stream.Lock(0,1); $stream.Unlock(0,1)
            } catch { throw 'Закройте Voice Bridge через меню значка в трее и повторите установку. Процессы Python не будут принудительно завершены.' }
            finally { if ($stream) { $stream.Dispose() } }
        }
    }
}
function Download-File([string]$Url,[string]$Target) {
    Check-Cancel
    Write-Log ('Загрузка: ' + $Url)
    $client = New-Object Net.WebClient
    $partial = $Target + '.partial'
    try {
        $task = $client.DownloadFileTaskAsync([Uri]$Url,$partial)
        $start = Get-Date
        while (-not $task.IsCompleted) {
            if ((Test-Path -LiteralPath $CancelFile) -or ((Get-Date)-$start).TotalMinutes -gt 20) {
                $client.CancelAsync(); throw 'Загрузка отменена или превышен таймаут. Повторите установку.'
            }
            Start-Sleep -Milliseconds 200
        }
        $task.GetAwaiter().GetResult()
        Move-Item -LiteralPath $partial -Destination $Target -Force
        Write-Log ('SHA256: ' + (Get-FileHash -Algorithm SHA256 -LiteralPath $Target).Hash)
    } finally { $client.Dispose(); if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force } }
}
function Quote-Arg([string]$Value) {
    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) { throw 'Недопустимый аргумент установщика.' }
    return '"' + $Value + '"'
}
function Wait-Retry([int]$Seconds) {
    for ($i=0; $i -lt ($Seconds*5); $i++) { Check-Cancel; Start-Sleep -Milliseconds 200 }
}
function Run-Program([string]$Exe,[string[]]$Arguments,[switch]$RetryFileLocks) {
    # Only pip install callers opt into retry. Never retry an arbitrary side effect.
    $delays=@(3,8,20)
    $maxAttempts=1
    if ($RetryFileLocks) { $maxAttempts=4 }
    for ($attempt=1; $attempt -le $maxAttempts; $attempt++) {
        Check-Cancel
        $prefix = Join-Path $Stage ([Guid]::NewGuid().ToString('N'))
        $outFile=$prefix+'.out';$errFile=$prefix+'.err'
        $effective=@($Arguments)
        if ($attempt -gt 1) {
            # A failed install may have written dist-info before being interrupted.
            # Reinstall from verified/local or pip-cached wheels rather than trusting it.
            $effective+=@('--force-reinstall')
        }
        $cmdline = ($effective | ForEach-Object { Quote-Arg $_ }) -join ' '
        Write-Log ('Запуск: ' + [IO.Path]::GetFileName($Exe) + ' ' + $cmdline)
        # Process-local TEMP/TMP only. Do not alter system/user environment settings.
        $privateTemp=Join-Path (Join-Path $Data 'setup-tmp') ([Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $privateTemp -Force | Out-Null
        $oldTemp=$env:TEMP;$oldTmp=$env:TMP
        $p=$null;$exitCode=-1;$combined=''
        try {
            $env:TEMP=$privateTemp;$env:TMP=$privateTemp
            $p = Start-Process -FilePath $Exe -ArgumentList $cmdline -WorkingDirectory $Stage -WindowStyle Hidden -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
            $keepHandle=$p.Handle
            $seen=@{out=0;err=0}
            while ($true) {
                $exited=$p.HasExited
                if ($exited) { $p.WaitForExit() }
                # Drain output after exit as well, so the final error is not lost.
                foreach ($kind in @('out','err')) {
                    $f=$prefix+'.'+$kind
                    if (Test-Path -LiteralPath $f) {
                        $content=Get-Content -LiteralPath $f -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
                        if ($content -and $content.Length -gt $seen[$kind]) {
                            [IO.File]::AppendAllText($Log,$content.Substring($seen[$kind]),$Utf8)
                            $seen[$kind]=$content.Length
                        }
                    }
                }
                if ($exited) { break }
                if (Test-Path -LiteralPath $CancelFile) {
                    # Direct child only: neither taskkill python.exe nor other apps.
                    if (-not $p.HasExited) { $p.Kill() }
                    $p.WaitForExit();throw 'Установка отменена пользователем.'
                }
                Start-Sleep -Milliseconds 300
            }
            $exitCode=$p.ExitCode
            foreach ($f in @($outFile,$errFile)) {
                if (Test-Path -LiteralPath $f) { $combined+=[IO.File]::ReadAllText($f) }
            }
        } finally {
            $env:TEMP=$oldTemp;$env:TMP=$oldTmp
            if ($p) { $p.Dispose() }
            # Best effort only; a locked temporary file cannot invalidate a good install.
            Remove-Item -LiteralPath $privateTemp -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($exitCode -eq 0) { return }
        Check-Cancel
        if ($RetryFileLocks -and $combined -match '\[WinError (32|33)\]') {
            if ($attempt -lt $maxAttempts) {
                Write-Log ('Windows временно заблокировала файл. Повтор команды '+($attempt+1)+'/'+$maxAttempts+' через '+$delays[$attempt-1]+' с. Кэш PyTorch сохраняется.')
                Wait-Retry $delays[$attempt-1]
                continue
            }
            throw 'Файл остаётся занят после четырёх попыток. Кэш PyTorch сохранён. Закройте другие установщики, перезагрузите Windows при необходимости и запустите этот установщик повторно. Подробности в журнале; --user и отключение защиты не требуются.'
        }
        throw ('Команда завершилась с кодом ' + $exitCode + '. Подробности в журнале.')
    }
}

try {
    try { $held=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held=$true }
    if (-not $held) { throw 'Другой установщик Voice Bridge уже запущен.' }
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Требуется Windows x64.' }
    Assert-Stopped
    New-Item -ItemType Directory -Path $Root,$Data -Force | Out-Null
    $version='0.5.4-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
    $Stage=Join-Path (Join-Path $Root 'versions') $version
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null
    # Verify all bundled payload files before running local code.
    $manifest=Get-Content -LiteralPath (Join-Path $Source 'payload-hashes.json') -Raw | ConvertFrom-Json
    foreach ($item in $manifest.PSObject.Properties) {
        $file=Join-Path $Source $item.Name
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.Value) { throw ('Повреждён файл пакета: '+$item.Name) }
    }
    $runtime=Join-Path $Stage 'runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $pythonZip=Join-Path $Stage 'python.zip'
    Download-File 'https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip' $pythonZip
    Expand-Archive -LiteralPath $pythonZip -DestinationPath $runtime
    $python=Join-Path $runtime 'python.exe'
    $signature=Get-AuthenticodeSignature -LiteralPath $python
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw 'Не удалось подтвердить подпись Python Software Foundation. Установка остановлена; проверка подписи не отключается.'
    }
    [IO.File]::WriteAllText((Join-Path $runtime 'python313._pth'),"python313.zip`r`n.`r`nLib/site-packages`r`nimport site`r`n",[Text.Encoding]::ASCII)
    $site=Join-Path $runtime 'Lib\site-packages'
    New-Item -ItemType Directory -Path $site -Force | Out-Null
    # Test BOTH first install and replacement before downloading large dependencies.
    Run-Program $python @('-I','-X','utf8',(Join-Path $Source 'activation.py'),'self-test')
    # Bootstrap only an exact pure-Python pip wheel, never execute get-pip.py.
    $metaFile=Join-Path $Stage 'pip-metadata.json'
    Download-File 'https://pypi.org/pypi/pip/25.1.1/json' $metaFile
    $meta=Get-Content -LiteralPath $metaFile -Raw | ConvertFrom-Json
    $pipFile=@($meta.urls | Where-Object { $_.filename -eq 'pip-25.1.1-py3-none-any.whl' })
    if ($pipFile.Count -ne 1 -or -not $pipFile[0].url.StartsWith('https://files.pythonhosted.org/')) { throw 'Некорректные метаданные pip.' }
    $pipZip=Join-Path $Stage 'pip.zip'
    Download-File $pipFile[0].url $pipZip
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $pipZip).Hash.ToLowerInvariant() -ne $pipFile[0].digests.sha256) { throw 'SHA256 pip не совпал.' }
    Expand-Archive -LiteralPath $pipZip -DestinationPath $site
    $env:PYTHONUTF8='1';$env:PYTHONNOUSERSITE='1';$env:PIP_DISABLE_PIP_VERSION_CHECK='1'
    $wheelCache=Join-Path $Data 'downloads\wheels'
    $pipCache=Join-Path $Data 'downloads\pip-cache'
    New-Item -ItemType Directory -Path $wheelCache,$pipCache -Force | Out-Null
    Write-Log ('Постоянный кэш загрузок: ' + (Join-Path $Data 'downloads'))
    $pipBase=@('-X','utf8','-m','pip','--isolated','install','--only-binary=:all:','--disable-pip-version-check','--no-warn-script-location','--progress-bar','off','--cache-dir',$pipCache,'--index-url','https://pypi.org/simple')
    Run-Program $python ($pipBase+@('PySide6==6.10.3','numpy==2.3.3','sounddevice==0.5.5','--report',(Join-Path $Stage 'core-install.json'))) -RetryFileLocks
    $wheel=Join-Path $Source 'voice_input_bridge-0.5.4-py3-none-any.whl'
    Run-Program $python @('-X','utf8','-m','pip','--isolated','install','--no-deps','--no-index','--no-warn-script-location',$wheel) -RetryFileLocks
    if ($Profile -ne 'delivery') {
        # Download the large wheel OUTSIDE pip-unpack and keep it across attempts.
        $torchResult=Join-Path $Stage 'torch-cache.json'
        Run-Program $python @('-X','utf8',(Join-Path $Source 'cache_wheel.py'),'--profile',$Profile,'--cache',$wheelCache,'--result',$torchResult,'--cancel',$CancelFile)
        $torch=Get-Content -LiteralPath $torchResult -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not (Test-Path -LiteralPath $torch.path)) { throw 'Проверенный wheel PyTorch отсутствует.' }
        Write-Log ('Установка PyTorch из проверенного локального файла: '+$torch.path)
        # Only its small dependencies use the index; the large wheel is a local path.
        Run-Program $python ($pipBase+@([string]$torch.path,'--report',(Join-Path $Stage 'torch-install.json'))) -RetryFileLocks
        $constraints=Join-Path $Stage 'asr-constraints.txt'
        $torchBuild=$(if ($Profile -eq 'cuda') {'cu128'} else {'cpu'})
        [IO.File]::WriteAllText($constraints,('torch==2.9.1+'+$torchBuild+"`r`nnumpy==2.3.3`r`n"),[Text.Encoding]::ASCII)
        Run-Program $python ($pipBase+@('--constraint',$constraints,'--find-links',([IO.Path]::GetDirectoryName([string]$torch.path)),'transformers==5.13.1','accelerate==1.12.0','librosa==0.11.0','--report',(Join-Path $Stage 'asr-install.json'))) -RetryFileLocks
        $verify=Join-Path $Source 'verify-runtime.py'
        Run-Program $python @('-X','utf8',$verify,'--asr')
    } else { Run-Program $python @('-X','utf8',(Join-Path $Source 'verify-runtime.py')) }
    Run-Program $python @('-X','utf8','-m','pip','--isolated','check')
    $ReadyForActivation=$true
    @{version='0.5.4';profile=$Profile;ready=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Stage 'ready-to-activate.json') -Encoding UTF8
    Check-Cancel
    Assert-Stopped
    Copy-Item -LiteralPath (Join-Path $Source 'VoiceBridge.exe') -Destination (Join-Path $Root 'VoiceBridge.exe') -Force
    Copy-Item -LiteralPath (Join-Path $Source 'uninstall.ps1') -Destination (Join-Path $Root 'uninstall.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $Source 'activation.py') -Destination (Join-Path $Root 'activation.py') -Force
    Copy-Item -LiteralPath (Join-Path $Source 'rollback.ps1') -Destination (Join-Path $Root 'rollback.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $Source 'README_WINDOWS_RU.md') -Destination (Join-Path $Root 'README_WINDOWS_RU.md') -Force
    $pythonw=Join-Path $runtime 'pythonw.exe'
    Write-Log 'Активация проверенного окружения; предыдущий current.ini будет сохранён.'
    Run-Program $python @('-I','-X','utf8',(Join-Path $Source 'activation.py'),'activate','--root',$Root,'--pythonw',$pythonw,'--version','0.5.4')
    $Activated=$true
    $shell=New-Object -ComObject WScript.Shell
    $menu=Join-Path ([Environment]::GetFolderPath('Programs')) 'Voice Bridge'
    New-Item -ItemType Directory -Path $menu -Force | Out-Null
    foreach ($link in @(@('Voice Bridge.lnk',''),@('Voice Bridge - проверка вставки.lnk','--diagnostics'))) {
        $shortcut=$shell.CreateShortcut((Join-Path $menu $link[0]));$shortcut.TargetPath=Join-Path $Root 'VoiceBridge.exe'
        $shortcut.Arguments=$link[1];$shortcut.WorkingDirectory=$Root;$shortcut.Save()
    }
    $shortcut=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Voice Bridge.lnk'))
    $shortcut.TargetPath=Join-Path $Root 'VoiceBridge.exe';$shortcut.WorkingDirectory=$Root;$shortcut.Save()
    $reg='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceBridge'
    New-Item -Path $reg -Force | Out-Null
    $ps=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $uninstall='"'+$ps+'" -NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $Root 'uninstall.ps1')+'"'
    foreach ($entry in @{DisplayName='Voice Bridge';DisplayVersion='0.5.4';Publisher='Voice Bridge';InstallLocation=$Root;DisplayIcon=(Join-Path $Root 'VoiceBridge.exe');UninstallString=$uninstall}.GetEnumerator()) {
        New-ItemProperty -Path $reg -Name $entry.Key -Value $entry.Value -PropertyType String -Force | Out-Null
    }
    $runKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    if (Get-ItemProperty -LiteralPath $runKey -Name VoiceBridge -ErrorAction SilentlyContinue) {
        Set-ItemProperty -LiteralPath $runKey -Name VoiceBridge -Value ('"'+(Join-Path $Root 'VoiceBridge.exe')+'" --background')
    }
    Write-Log 'ГОТОВО. Приложение установлено. Откройте раздел Модели: выберите локальную папку или скачайте Qwen3-ASR прямо из приложения.'
    @{ok=$true;root=$Root;profile=$Profile;version='0.5.4'} | ConvertTo-Json | Set-Content -LiteralPath $Result -Encoding UTF8
} catch {
    # A child can commit current.ini just before logging/result delivery fails.
    # Inspect the pointer rather than claiming it is still old solely from a flag.
    if (-not $Activated -and $Stage) {
        try {
            $activeIni=Join-Path $Root 'current.ini'
            $expected='python='+(Join-Path (Join-Path $Stage 'runtime') 'pythonw.exe')
            if ((Test-Path -LiteralPath $activeIni) -and ([IO.File]::ReadAllLines($activeIni) -contains $expected)) { $Activated=$true }
        } catch { }
    }
    Write-Log ('ОШИБКА: '+$_.Exception.Message)
    if ($_.ScriptStackTrace) { Write-Log ('Место сбоя: '+$_.ScriptStackTrace) }
    Write-Log ('Сохранённые загрузки находятся в '+(Join-Path $Data 'downloads')+'. Повторный запуск 0.5.4 использует проверенный кэш.')
    if ($Activated) { Write-Log 'Окружение уже активировано; ошибка возникла при завершении установки.' }
    else { Write-Log 'Незавершённое окружение не активировано; прежняя версия, если была, остаётся выбранной.' }
    if ($ReadyForActivation -and -not $Activated) { Write-Log ('Проверенное окружение сохранено: '+$Stage+'. Оно не удаляется после ошибки финализации.') }
    @{ok=$false;error=$_.Exception.Message;activated=$Activated;readyForActivation=$ReadyForActivation;stage=$Stage} | ConvertTo-Json | Set-Content -LiteralPath $Result -Encoding UTF8
    if ($Stage -and -not $Activated -and -not $ReadyForActivation -and (Test-Path -LiteralPath $Stage)) {
        Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
