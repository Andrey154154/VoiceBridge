param([Parameter(Mandatory=$true)][string]$Payload)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$work=Join-Path $PSScriptRoot 'content'
try { Expand-Archive -LiteralPath $Payload -DestinationPath $work -Force }
catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Voice Bridge');exit 1 }
$logDir=Join-Path $env:LOCALAPPDATA 'VoiceBridge\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log=Join-Path $logDir ('setup-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
[IO.File]::WriteAllText($log,'',[Text.Encoding]::UTF8)
$result=Join-Path $PSScriptRoot 'result.json'
$cancel=Join-Path $PSScriptRoot 'cancel'
$bg=[Drawing.ColorTranslator]::FromHtml('#0C111B')
$fg=[Drawing.ColorTranslator]::FromHtml('#E8EDF5')
$muted=[Drawing.ColorTranslator]::FromHtml('#A2AEC1')
$panel=[Drawing.ColorTranslator]::FromHtml('#172334')
$accent=[Drawing.ColorTranslator]::FromHtml('#66DFC5')
$form=New-Object Windows.Forms.Form
$form.Text='Voice Bridge 0.5.4 — установка'
$form.ClientSize=New-Object Drawing.Size(780,620)
$form.MinimumSize=New-Object Drawing.Size(700,580)
$form.StartPosition='CenterScreen'
$form.AutoScaleMode=[Windows.Forms.AutoScaleMode]::Dpi
$form.BackColor=$bg;$form.ForeColor=$fg
$form.Font=New-Object Drawing.Font('Segoe UI',10)
$layout=New-Object Windows.Forms.TableLayoutPanel
$layout.Dock='Fill';$layout.ColumnCount=1;$layout.RowCount=9
$layout.Padding=New-Object Windows.Forms.Padding(26)
foreach ($height in @(42,30,80,54,34,24,18,0,55)) {
    if ($height -eq 0) { $style=New-Object Windows.Forms.RowStyle('Percent',100) }
    else { $style=New-Object Windows.Forms.RowStyle('Absolute',$height) }
    [void]$layout.RowStyles.Add($style)
}
$form.Controls.Add($layout)
$title=New-Object Windows.Forms.Label;$title.Text='Voice Bridge';$title.Dock='Fill'
$title.Font=New-Object Drawing.Font('Segoe UI',24,[Drawing.FontStyle]::Bold);$layout.Controls.Add($title,0,0)
$tag=New-Object Windows.Forms.Label;$tag.Text='ВАШ ГОЛОС. ВАШ КОМПЬЮТЕР.';$tag.ForeColor=$accent;$tag.Dock='Fill'
$tag.Font=New-Object Drawing.Font('Segoe UI',9,[Drawing.FontStyle]::Bold);$layout.Controls.Add($tag,0,1)
$intro=New-Object Windows.Forms.Label;$intro.Dock='Fill';$intro.ForeColor=$muted
$intro.Text="Локальная диктовка в браузере, редакторе и других приложениях.`r`nПосле установки выберите свою модель или скачайте Qwen3-ASR из приложения.`r`nСистемный Python не меняется. Перед обновлением закройте Voice Bridge в трее."
$layout.Controls.Add($intro,0,2)
$choice=New-Object Windows.Forms.ComboBox;$choice.DropDownStyle='DropDownList';$choice.Dock='Top'
$choice.BackColor=$panel;$choice.ForeColor=$fg;$choice.FlatStyle='Flat'
[void]$choice.Items.Add('NVIDIA — приложение и распознавание на видеокарте')
[void]$choice.Items.Add('CPU — приложение и распознавание на процессоре')
[void]$choice.Items.Add('Проверка интерфейса и вставки — без библиотек распознавания')
$choice.SelectedIndex=0;$layout.Controls.Add($choice,0,3)
$status=New-Object Windows.Forms.Label;$status.Text='Выберите вариант установки. Для загрузки компонентов нужен интернет.';$status.Dock='Fill'
$layout.Controls.Add($status,0,4)
$progress=New-Object Windows.Forms.ProgressBar;$progress.Dock='Top';$progress.Style='Continuous';$progress.Height=10
$layout.Controls.Add($progress,0,5)
$details=New-Object Windows.Forms.CheckBox;$details.Text='Показать технический журнал';$details.Dock='Fill';$details.ForeColor=$muted
$layout.Controls.Add($details,0,6)
$body=New-Object Windows.Forms.Panel;$body.Dock='Fill';$layout.Controls.Add($body,0,7)
$text=New-Object Windows.Forms.TextBox;$text.Multiline=$true;$text.ReadOnly=$true;$text.ScrollBars='Both'
$text.WordWrap=$false;$text.Dock='Fill';$text.Font=New-Object Drawing.Font('Consolas',9)
$text.BackColor=$panel;$text.ForeColor=$fg;$text.BorderStyle='FixedSingle';$text.Visible=$false
$body.Controls.Add($text)
$hint=New-Object Windows.Forms.Label;$hint.Dock='Fill';$hint.ForeColor=$muted
$hint.Padding=New-Object Windows.Forms.Padding(0,18,0,0)
$hint.Text="1. Установите приложение и библиотеки распознавания.`r`n`r`n2. Выберите локальную модель или скачайте её в разделе Модели.`r`n`r`n3. Проверьте вставку, выберите микрофон — и начинайте диктовать.`r`n`r`nЗагрузки PyTorch сохраняются между попытками установки."
$body.Controls.Add($hint)
$details.Add_CheckedChanged({$text.Visible=$details.Checked;$hint.Visible=-not $details.Checked})
$buttons=New-Object Windows.Forms.FlowLayoutPanel;$buttons.Dock='Fill';$buttons.Padding=New-Object Windows.Forms.Padding(0,12,0,0)
function Make-Button([string]$Caption,[int]$Width) {
    $b=New-Object Windows.Forms.Button;$b.Text=$Caption;$b.Width=$Width;$b.Height=34
    $b.FlatStyle='Flat';$b.BackColor=$panel;$b.ForeColor=$fg;$b.FlatAppearance.BorderColor=$panel
    return $b
}
$start=Make-Button 'Установить' 130;$start.BackColor=$accent;$start.ForeColor=$bg
$stop=Make-Button 'Отмена' 90;$stop.Enabled=$false
$launch=Make-Button 'Открыть Voice Bridge' 200;$launch.Enabled=$false
$logs=Make-Button 'Журнал' 100
$buttons.Controls.AddRange(@($start,$stop,$launch,$logs));$layout.Controls.Add($buttons,0,8)
$script:running=$false;$script:child=$null;$script:installedRoot=$null
$timer=New-Object Windows.Forms.Timer;$timer.Interval=500
$timer.Add_Tick({
    try {
        $content=Get-Content -LiteralPath $log -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($content -and $text.Text -ne $content) {
            $text.Text=$content;$text.SelectionStart=$text.Text.Length;$text.ScrollToCaret()
            if ($script:running) {
                if ($content -match 'asr-install.json') { $status.Text='Устанавливаем библиотеки распознавания...' }
                elseif ($content -match 'torch-cache.json') { $status.Text='Подготавливаем PyTorch. Большой пакет сохраняется в кэш...' }
                elseif ($content -match 'core-install.json') { $status.Text='Устанавливаем интерфейс и аудиокомпоненты...' }
                else { $status.Text='Подготавливаем отдельное окружение приложения...' }
            }
        }
        if ($script:running -and $script:child.HasExited) {
            $script:running=$false;$stop.Enabled=$false;$start.Enabled=$true;$choice.Enabled=$true
            $progress.Style='Continuous'
            if (Test-Path -LiteralPath $result) {
                $r=Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
                if ($r.ok) {
                    $script:installedRoot=$r.root;$launch.Enabled=$true;$status.Text='Установлено. Откройте приложение и выберите модель.';$progress.Value=100
                    $hint.Text="Приложение установлено.`r`n`r`nВ разделе Модели можно выбрать папку готовой модели или скачать Qwen3-ASR.`r`n`r`nПеред первой диктовкой выполните проверку вставки."
                } else {
                    $status.Text='Установка не завершена. Загрузки сохранены; можно повторить.';$start.Text='Повторить';$details.Checked=$true
                }
            } else {$status.Text='Нет подтверждения установки. Откройте журнал.';$details.Checked=$true}
            $script:child.Dispose();$script:child=$null
        }
    } catch {$status.Text=$_.Exception.Message}
})
$start.Add_Click({
    try {
        Remove-Item -LiteralPath $result,$cancel -Force -ErrorAction SilentlyContinue
        $profile=@('cuda','cpu','delivery')[$choice.SelectedIndex]
        $args='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $work 'setup-worker.ps1')+'" -Source "'+$work+'" -Profile '+$profile+' -Log "'+$log+'" -Result "'+$result+'" -CancelFile "'+$cancel+'"'
        $ps=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $script:child=Start-Process -FilePath $ps -ArgumentList $args -WindowStyle Hidden -PassThru
        $script:running=$true;$start.Enabled=$false;$stop.Enabled=$true;$launch.Enabled=$false;$choice.Enabled=$false
        $progress.Style='Marquee';$status.Text='Подготовка установки...'
    } catch {[Windows.Forms.MessageBox]::Show($_.Exception.Message,'Voice Bridge') | Out-Null}
})
$stop.Add_Click({[IO.File]::WriteAllText($cancel,'cancel');$stop.Enabled=$false;$status.Text='Отмена установки... Загрузки сохраняются.'})
$logs.Add_Click({Start-Process -FilePath 'notepad.exe' -ArgumentList ('"'+$log+'"')})
$launch.Add_Click({Start-Process -FilePath (Join-Path $script:installedRoot 'VoiceBridge.exe');$form.Close()})
$form.Add_FormClosing({param($sender,$event)
    if ($script:running) {$event.Cancel=$true;[IO.File]::WriteAllText($cancel,'cancel');$status.Text='Отмена установки... После завершения окно можно закрыть.'}
})
$timer.Start()
try {[void]$form.ShowDialog()}
finally {
    $timer.Stop();$timer.Dispose();$form.Dispose()
    if (-not $script:running) {Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue}
}
