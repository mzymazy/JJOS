# JJOS 冷启动冒烟测试（Windows）
#
# 目的：在"没有 Python 的机器"上验证一键安装全链路是否还走得通。
# 做法：把 PATH 缩到只剩系统目录（python/py/git 全部不可见），把下载物隔离到
#       临时目录，然后真跑一次 bootstrap-install.ps1，检查产物是否齐全。
#
# 为什么用"摘 PATH"而不是 venv：venv 的前提是有 Python，而这里要测的正是
# "机器上根本没有 Python"的场景。
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File tests\cold_start_smoke.ps1
#   powershell -ExecutionPolicy Bypass -File tests\cold_start_smoke.ps1 -UseCache
#   powershell -ExecutionPolicy Bypass -File tests\cold_start_smoke.ps1 -KeepArtifacts
#
# 参数：
#   -UseCache      复用上次下载的 Python（跳过 49MB 下载），适合只验证编排逻辑
#   -KeepArtifacts 保留临时目录，便于失败时排查
#
# 退出码：0=通过，1=失败。首次运行需要联网（约 50MB Python + 14MB ComfyUI）。
# 注意：这是**真机冒烟**，会访问网络，不适合放进单元测试的默认批次。

param(
    [switch]$UseCache,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$repo = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $repo 'bootstrap-install.ps1'
$testRoot = Join-Path $env:TEMP 'jjos-cold-smoke'
$comfyTarget = Join-Path $testRoot 'ComfyUI'

if (-not (Test-Path $entry)) {
    Write-Host "[X] 找不到 $entry" -ForegroundColor Red
    exit 1
}

# 复用缓存时保留上次的 runtime，否则清空重来
if (-not $UseCache -and (Test-Path $testRoot)) {
    Remove-Item $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
New-Item -ItemType Directory -Force -Path $comfyTarget | Out-Null

# 记录测试前仓库根已有哪些产物，结束时只删本次新建的，绝不碰用户自己的
$cfgPath = Join-Path $repo 'config.json'
$batPath = Join-Path $repo '一键启动.bat'
$shPath = Join-Path $repo '一键启动.sh'
$preExisting = @{}
foreach ($p in @($cfgPath, $batPath, $shPath)) { $preExisting[$p] = (Test-Path $p) }

# 模拟裸机：只留系统目录，摘掉 python / py / git
$cleanPath = 'C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0'
$env:PATH = $cleanPath
$env:LOCALAPPDATA = $testRoot
$env:JJOS_COMFY_DIR = $comfyTarget
$env:JJOS_NO_PAUSE = '1'

Write-Host '============================================' -ForegroundColor Cyan
Write-Host '  JJOS 冷启动冒烟测试（模拟没装 Python 的机器）' -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan

$leaked = @()
foreach ($n in @('python', 'py', 'python3', 'git')) {
    if (Get-Command $n -ErrorAction SilentlyContinue) { $leaked += $n }
}
if ($leaked.Count -gt 0) {
    Write-Host "[X] 环境没隔离干净，仍能看到：$($leaked -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host '环境隔离 OK：python / py / git 均不可见' -ForegroundColor Green
Write-Host "临时目录：$testRoot" -ForegroundColor DarkGray
Write-Host ''

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$output = & powershell -NoProfile -ExecutionPolicy Bypass -File $entry 2>&1 | Out-String
$code = $LASTEXITCODE
$sw.Stop()

Write-Output $output

# ---- 断言 ----
$fails = @()
if ($code -ne 0) { $fails += "退出码非 0（$code）" }
if (-not (Test-Path (Join-Path $comfyTarget 'main.py'))) { $fails += 'ComfyUI main.py 未落地' }
if (-not (Test-Path $cfgPath)) { $fails += 'config.json 未生成' }
if (-not (Test-Path $batPath)) { $fails += '一键启动.bat 未生成' }
if ($output -match 'NativeCommandError') { $fails += '输出里出现 NativeCommandError（下载误报又回来了）' }
if ($output -match 'PARSE ERROR') { $fails += 'PowerShell 解析出错' }

# ---- 收尾：只删本次新建的产物 ----
foreach ($p in @($cfgPath, $batPath, $shPath)) {
    if (-not $preExisting[$p] -and (Test-Path $p)) {
        Remove-Item $p -Force -ErrorAction SilentlyContinue
    }
}
if (-not $KeepArtifacts -and -not $UseCache) {
    Remove-Item $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($fails.Count -eq 0) {
    Write-Host ('PASS  冷启动链路通过，用时 {0:N0} 秒' -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
    if ($KeepArtifacts) { Write-Host "产物保留在：$testRoot" -ForegroundColor DarkGray }
    exit 0
}
Write-Host 'FAIL  冷启动链路有问题：' -ForegroundColor Red
$fails | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
if (-not $KeepArtifacts) {
    Write-Host '（用 -KeepArtifacts 重跑可保留现场）' -ForegroundColor Yellow
}
exit 1
