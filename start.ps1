<#
.SYNOPSIS
  Запуск РечьКонтроль одной командой (Windows, PowerShell 5.1+ или pwsh 7).

.DESCRIPTION
  По умолчанию: Docker Desktop + docker compose (приложение и PostgreSQL 17).
  1. Проверяет, что Docker установлен и запущен (при необходимости запускает Docker Desktop).
  2. Создаёт .env со случайным паролем PostgreSQL, если его нет.
  3. docker compose up -d --build (с ключом -Llm ещё и Ollama, профиль llm).
  4. Ждёт /api/health, печатает стартовые логины и пароли, открывает браузер.
  Без Docker (или с ключом -NoDocker): venv + pip install + python run.py, база SQLite в data\.

.EXAMPLE
  .\start.bat
  .\start.bat -Llm
  .\start.bat -NoDocker
  .\start.bat -Stop
#>
[CmdletBinding()]
param(
    [switch]$Llm,        # поднять Ollama (профиль llm) и включить оценку LLM
    [switch]$NoDocker,   # запуск без Docker: venv + run.py
    [switch]$NoBrowser,  # не открывать браузер
    [switch]$Stop,       # остановить контейнеры (docker compose down, данные остаются)
    [int]$Port = 0,      # порт на 127.0.0.1 (по умолчанию APP_PORT из .env или 8080)
    [int]$TimeoutSec = 900
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8
$OnWindows = ($PSVersionTable.PSEdition -eq "Desktop") -or $IsWindows

function Say([string]$Text, [string]$Color = "Gray") { Write-Host $Text -ForegroundColor $Color }
function Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host ""; Write-Host "ОШИБКА: $Text" -ForegroundColor Red; exit 1 }

function New-Secret([int]$Bytes = 24) {
    $buf = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buf)
    $rng.Dispose()
    return (($buf | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Write-Utf8NoBom([string]$Path, [string[]]$Lines) {
    $full = Join-Path (Get-Location) $Path
    [System.IO.File]::WriteAllText($full, (($Lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

function Read-EnvLines {
    if (-not (Test-Path -LiteralPath ".env")) { return @() }
    return [System.IO.File]::ReadAllLines((Join-Path (Get-Location) ".env"), [System.Text.Encoding]::UTF8)
}

function Get-EnvValue([string[]]$Lines, [string]$Key) {
    foreach ($line in $Lines) {
        if ($line -match "^\s*$Key\s*=(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return ""
}

function Set-EnvValue([string[]]$Lines, [string]$Key, [string]$Value) {
    $found = $false
    $out = foreach ($line in $Lines) {
        if ($line -match "^\s*$Key\s*=") { $found = $true; "$Key=$Value" } else { $line }
    }
    if (-not $found) { $out = @($out) + "$Key=$Value" }
    return ,@($out)
}

function Initialize-EnvFile([bool]$ForDocker) {
    $lines = Read-EnvLines
    $created = $false
    if ($lines.Count -eq 0) {
        if (-not (Test-Path -LiteralPath ".env.example")) { Fail "нет .env.example рядом со скриптом" }
        $lines = [System.IO.File]::ReadAllLines((Join-Path (Get-Location) ".env.example"), [System.Text.Encoding]::UTF8)
        $created = $true
    }
    $changed = $created
    if ($ForDocker -and -not (Get-EnvValue $lines "POSTGRES_PASSWORD")) {
        $lines = Set-EnvValue $lines "POSTGRES_PASSWORD" (New-Secret 24)
        $changed = $true
        Say "  пароль PostgreSQL сгенерирован и записан в .env (POSTGRES_PASSWORD)"
    }
    if ($Llm -and (Get-EnvValue $lines "LLM_PROVIDER") -ne "ollama") {
        $lines = Set-EnvValue $lines "LLM_PROVIDER" "ollama"
        $changed = $true
        Say "  LLM_PROVIDER=ollama записан в .env"
    }
    if (-not $ForDocker -and -not (Get-EnvValue $lines "MASTER_KEY_FILE") -and -not (Get-EnvValue $lines "MASTER_KEY")) {
        # 152-ФЗ: ключ шифрования не держим в data\ рядом с данными. Старый data\master.key программа перенесёт сама.
        $keyDir = if ($OnWindows) { Join-Path $env:LOCALAPPDATA "RechKontrol" } else { Join-Path $HOME ".config/rechkontrol" }
        $lines = Set-EnvValue $lines "MASTER_KEY_FILE" (Join-Path $keyDir "master.key")
        $changed = $true
        Say "  ключ шифрования будет храниться вне data: $(Join-Path $keyDir 'master.key') (MASTER_KEY_FILE в .env)" "Yellow"
    }
    if ((Get-EnvValue $lines "WHISPER_MODEL") -eq "base" -and -not ($lines -match "WHISPER_MODEL_MIGRATED")) {
        # base был старым значением по умолчанию; small заметно грамотнее. Один раз переводим.
        $lines = Set-EnvValue $lines "WHISPER_MODEL" "small"
        $lines = @($lines) + "# WHISPER_MODEL_MIGRATED: base -> small (вернуть base можно руками, больше не трогаем)"
        $changed = $true
        Say "  WHISPER_MODEL: base -> small (точнее распознаёт; первый звонок докачает ~480 МБ)" "Yellow"
    }
    if ($changed) { Write-Utf8NoBom ".env" $lines }
    if ($created) { Say "  создан .env из .env.example" "Green" } else { Say "  .env уже есть, используется он" }
    return $lines
}

function Test-Health([int]$P) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$P/api/health" -TimeoutSec 3 -UseBasicParsing
        return ($r.status -eq "ok")
    } catch { return $false }
}

function Wait-Health([int]$P, [int]$Seconds, [scriptblock]$Alive) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    $dots = 0
    while ((Get-Date) -lt $deadline) {
        if (Test-Health $P) { Write-Host ""; return $true }
        if ($Alive -and -not (& $Alive)) { Write-Host ""; return $false }
        Write-Host -NoNewline "."
        $dots++
        Start-Sleep -Seconds 3
    }
    Write-Host ""
    return $false
}

function Show-Credentials([string]$Text, [int]$P) {
    $rows = @()
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("`t")) {
            $parts = $line.Split("`t")
            if ($parts.Count -ge 3) { $rows += , $parts }
        }
    }
    Step "Вход в кабинет: http://127.0.0.1:$P"
    if ($rows.Count -eq 0) {
        Say "  Стартовых паролей нет: они уже сменены при первом входе."
        Say "  Забыли пароль администратора - задайте ADMIN_PASSWORD в .env, см. docs/RUN.md."
        return
    }
    Say "  Стартовые учётные записи (при первом входе пароль нужно сменить):" "Yellow"
    foreach ($r in $rows) { Say ("  {0,-9} логин: {1,-12} пароль: {2}" -f $r[1], $r[0], $r[2]) "Yellow" }
}

function Test-DockerReady {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & docker info *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } finally { $ErrorActionPreference = $old }
}

function Start-DockerDesktop {
    if (-not $OnWindows) { return $false }
    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    )
    $exe = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $exe) { return $false }
    Say "  Docker Desktop не запущен, запускаю: $exe"
    Start-Process -FilePath $exe | Out-Null
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerReady) { Write-Host ""; return $true }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 4
    }
    Write-Host ""
    return $false
}

function Invoke-Compose([string[]]$ComposeArgs) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & docker compose @ComposeArgs 2>&1 | Out-Host; return $LASTEXITCODE } finally { $ErrorActionPreference = $old }
}

function Open-App([int]$P) {
    if ($NoBrowser) { return }
    try { Start-Process "http://127.0.0.1:$P" | Out-Null } catch { Say "  Откройте в браузере: http://127.0.0.1:$P" }
}

function Start-WithDocker {
    Step "Docker"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return "missing" }
    if (-not (Test-DockerReady)) {
        if (-not (Start-DockerDesktop)) { return "not-running" }
    }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & docker compose version *> $null
    $hasCompose = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $old
    if (-not $hasCompose) { Fail "нет docker compose v2. Обновите Docker Desktop." }
    Say "  Docker работает" "Green"

    Step "Настройки (.env)"
    $lines = Initialize-EnvFile $true
    $P = $Port
    if ($P -le 0) { $v = Get-EnvValue $lines "APP_PORT"; $P = if ($v) { [int]$v } else { 8080 } }
    $env:APP_PORT = "$P"

    Step "Сборка и запуск контейнеров (первый раз несколько минут)"
    $upArgs = @()
    if ($Llm) { $upArgs += @("--profile", "llm") }
    $upArgs += @("up", "-d", "--build")
    if ((Invoke-Compose $upArgs) -ne 0) { Fail "docker compose up завершился с ошибкой (см. вывод выше)" }

    if ($Llm) {
        $model = Get-EnvValue (Read-EnvLines) "LLM_MODEL"
        if (-not $model) { $model = "qwen2.5:7b-instruct" }
        Step "Модель Ollama $model"
        # Ollama в контейнере стартует пару секунд: ждём, пока ответит "ollama list".
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $list = ""
        for ($i = 0; $i -lt 30; $i++) {
            $list = (& docker compose --profile llm exec -T ollama ollama list 2>$null) -join "`n"
            if ($LASTEXITCODE -eq 0) { break }
            Start-Sleep -Seconds 2
        }
        $ErrorActionPreference = $old
        if ($list -match [regex]::Escape($model)) {
            Say "  уже скачана" "Green"
        } else {
            Say "  скачиваю (несколько ГБ, один раз)..."
            if ((Invoke-Compose @("--profile", "llm", "exec", "-T", "ollama", "ollama", "pull", $model)) -ne 0) {
                Say "  Не удалось скачать модель. Оценка пойдёт только по правилам. Повторите: docker compose exec ollama ollama pull $model" "Yellow"
            }
        }
    }

    Step "Ожидание http://127.0.0.1:$P/api/health"
    if (-not (Wait-Health $P $TimeoutSec $null)) {
        [void](Invoke-Compose @("logs", "--tail", "60", "app"))
        Fail "приложение не ответило за $TimeoutSec с. Логи: docker compose logs app"
    }
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $creds = (& docker compose exec -T app cat data/initial_credentials.txt 2>$null) -join "`n"
    $ErrorActionPreference = $old
    Show-Credentials $creds $P
    Say ""
    Say "  Остановить: .\start.bat -Stop   (данные в томах Docker сохраняются)"
    Say "  Логи:       docker compose logs -f app"
    Open-App $P
    return "ok"
}

function Find-Python {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @("py", "-3.12"); $candidates += , @("py", "-3") }
    $candidates += , @("python")
    $candidates += , @("python3")
    foreach ($c in $candidates) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $ver = (& $c[0] @($c | Select-Object -Skip 1) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
        $ErrorActionPreference = $old
        if ($LASTEXITCODE -eq 0 -and $ver -match "^3\.(\d+)$" -and [int]$Matches[1] -ge 10) { return , $c }
    }
    return $null
}

function Start-WithoutDocker {
    Step "Запуск без Docker (venv + SQLite)"
    $py = Find-Python
    if (-not $py) { Fail "нужен Python 3.10+ (рекомендуется 3.12): https://www.python.org/downloads/ или установите Docker Desktop." }
    $venvPy = if ($OnWindows) { ".venv\Scripts\python.exe" } else { ".venv/bin/python" }
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Say "  создаю .venv ($($py -join ' '))"
        & $py[0] @($py | Select-Object -Skip 1) -m venv .venv
        if ($LASTEXITCODE -ne 0) { Fail "не удалось создать .venv" }
    }
    Say "  ставлю зависимости (первый раз несколько минут)"
    & $venvPy -m pip install -q --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements.txt завершился с ошибкой" }

    Step "Настройки (.env)"
    $lines = Initialize-EnvFile $false
    $P = $Port
    if ($P -le 0) { $v = Get-EnvValue $lines "APP_PORT"; $P = if ($v) { [int]$v } else { 8080 } }
    if (Test-Health $P) {
        Say "  На 127.0.0.1:$P уже работает РечьКонтроль." "Green"
    } else {
        $env:APP_PORT = "$P"
        New-Item -ItemType Directory -Force -Path "data" | Out-Null
        $log = Join-Path (Get-Location) "data\server.log"
        if (-not $OnWindows) { $log = Join-Path (Get-Location) "data/server.log" }
        Step "Старт сервера (окно можно не трогать, журнал: $log)"
        if ($OnWindows) {
            $proc = Start-Process -FilePath $venvPy -ArgumentList "run.py" -PassThru -WindowStyle Minimized
        } else {
            $proc = Start-Process -FilePath $venvPy -ArgumentList "run.py" -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        }
        Step "Ожидание http://127.0.0.1:$P/api/health"
        if (-not (Wait-Health $P $TimeoutSec { -not $proc.HasExited })) {
            Fail "сервер не ответил. Запустите вручную: $venvPy run.py - ошибка будет в консоли."
        }
        Say "  Сервер запущен (PID $($proc.Id)). Остановить: $venvPy run.py --stop"
    }
    $credFile = if ($OnWindows) { "data\initial_credentials.txt" } else { "data/initial_credentials.txt" }
    $text = if (Test-Path -LiteralPath $credFile) { [System.IO.File]::ReadAllText((Join-Path (Get-Location) $credFile), [System.Text.Encoding]::UTF8) } else { "" }
    Show-Credentials $text $P
    Open-App $P
}

Say "РечьКонтроль - запуск" "White"

if ($Stop) {
    if ($Port -gt 0) { $env:APP_PORT = "$Port" }
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        [void](Invoke-Compose @("--profile", "llm", "down"))
        Say "Контейнеры остановлены, данные в томах сохранены." "Green"
    }
    $venvPy = if ($OnWindows) { ".venv\Scripts\python.exe" } else { ".venv/bin/python" }
    if (Test-Path -LiteralPath $venvPy) { & $venvPy run.py --stop }
    exit 0
}

if ($NoDocker) {
    Start-WithoutDocker
    exit 0
}

$result = Start-WithDocker
if ($result -eq "ok") { exit 0 }
if ($result -eq "missing") {
    Say "  Docker не найден. Установить: https://docs.docker.com/desktop/setup/install/windows-install/" "Yellow"
} else {
    Say "  Docker установлен, но не запущен (или не отвечает). Запустите Docker Desktop и повторите." "Yellow"
}
if ($Llm) { Say "  Ключ -Llm без Docker не работает: поставьте Ollama отдельно (docs/RUN.md)." "Yellow" }
Say "  Продолжаю без Docker: Python + SQLite." "Yellow"
Start-WithoutDocker
exit 0
