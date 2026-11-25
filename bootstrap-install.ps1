# JJOS 一键安装 · 自举段（Windows 自带 PowerShell 即可运行，不要求机器先装 Python）
#
# 它只解决一件事：给用户机器"搞到一个能用的 Python"，然后交给 bootstrap.py 做编排。
# 为什么要分两段：机器上没有 Python 时，任何 .py 安装脚本都跑不起来——这是死结。
# Windows 10/11 一定自带 PowerShell，让它来下载 + 校验 + 解压免安装 Python。
#
# 免安装 Python 来源：astral-sh/python-build-standalone（解压即用的完整 CPython，自带 pip）。
# 版本/哈希/大小来自该项目 GitHub Release 的官方 asset 元数据（2026-11-24 核实）：
#   cpython-3.11.18+20261124-x86_64-pc-windows-msvc-install_only.tar.gz
#   sha256 = 3e4a35e3c238e5090d36bf95c041d9052bc82d6e298cb912ba7f70f6c66cf81a
#   size   = 49096635
# 哈希强校验，因此下载走哪个镜像都安全；任一镜像内容不符立即丢弃重试。
# 可用环境变量覆盖（升级内置 Python 时改这三项即可，不必改逻辑）：
#   JJOS_PY_BUNDLE_VER / JJOS_PY_VERSION / JJOS_PY_SHA256 / JJOS_PY_SIZE / JJOS_PY_MIRROR

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# ---- 内置版本（官方 API 核实的真实值）----
$bundleVer = if ($env:JJOS_PY_BUNDLE_VER) { $env:JJOS_PY_BUNDLE_VER } else { '20261124' }
$pyVer     = if ($env:JJOS_PY_VERSION)    { $env:JJOS_PY_VERSION }    else { '3.11.18' }
$pySha     = if ($env:JJOS_PY_SHA256)     { $env:JJOS_PY_SHA256 }     else { '3e4a35e3c238e5090d36bf95c041d9052bc82d6e298cb912ba7f70f6c66cf81a' }
$pySize    = if ($env:JJOS_PY_SIZE)       { [int64]$env:JJOS_PY_SIZE } else { [int64]49096635 }

$asset = "cpython-$pyVer+$bundleVer-x86_64-pc-windows-msvc-install_only.tar.gz"
$assetEsc = $asset.Replace('+', '%2B')
$official = "https://github.com/astral-sh/python-build-standalone/releases/download/$bundleVer/$assetEsc"
# 官方源打不开时（国内常见）依次尝试镜像前缀；哈希校验保证镜像内容一致即可放心用。
$mirrorPrefixes = @('https://gh-proxy.com/', 'https://ghproxy.net/', '')
if ($env:JJOS_PY_MIRROR) { $mirrorPrefixes = @($env:JJOS_PY_MIRROR) + $mirrorPrefixes }

$jjosDir = $PSScriptRoot
$cacheDir = Join-Path $env:LOCALAPPDATA 'JJOS\runtime'
$downloadDir = Join-Path $cacheDir 'download'
$extractDir = Join-Path $cacheDir "python-$pyVer-$bundleVer"
$archive = Join-Path $downloadDir $asset

function Write-Step([string]$msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host $msg -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host $msg -ForegroundColor Yellow }
function Write-Bad([string]$msg)  { Write-Host $msg -ForegroundColor Red }

# ---- 1) 先找系统里有没有已经装好的 Python（>=3.9）----
function Get-PythonVersion {
    param([string]$Exe, [string[]]$Prefix = @())
    $probe = 'import sys;print(sys.version_info[0]);print(sys.version_info[1]);print(sys.executable)'
    try {
        $out = @(& $Exe @($Prefix + @('-c', $probe)) 2>$null)
        if ($out.Count -ge 3) {
            return @{ Major = [int]$out[0]; Minor = [int]$out[1]; Exe = $out[2].Trim() }
        }
    } catch { }
    return $null
}

function Get-PythonScore {
    param([int]$Major, [int]$Minor)
    if ($Major -ne 3 -or $Minor -lt 9) { return -1 }
    # torch wheel 覆盖面：3.11/3.12 全通道最稳；3.13 仅 cu126+；3.14 生态滞后。
    if ($Minor -eq 11 -or $Minor -eq 12) { return 100 }
    if ($Minor -eq 10) { return 80 }
    if ($Minor -eq 9)  { return 70 }
    if ($Minor -eq 13) { return 50 }
    return 20
}

function Resolve-SystemPython {
    $candidates = @()
    # py launcher 支持精确版本：py -3.11 / py -3.12 优先试，常能挑到机器上的老版本
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($m in @(12, 11, 10, 9)) {
            $v = Get-PythonVersion -Exe 'py' -Prefix @("-$m")
            if ($v) { $candidates += $v }
        }
        $v = Get-PythonVersion -Exe 'py' -Prefix @('-3')
        if ($v) { $candidates += $v }
    }
    foreach ($name in @('python', 'python3')) {
        if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { continue }
        $v = Get-PythonVersion -Exe $name
        if ($v) { $candidates += $v }
    }
    $best = $null; $bestScore = -1
    foreach ($v in $candidates) {
        if (-not (Test-Path $v.Exe)) { continue }
        $score = Get-PythonScore -Major $v.Major -Minor $v.Minor
        if ($score -gt $bestScore) { $bestScore = $score; $best = $v }
    }
    if ($best) {
        if ($bestScore -le 50) {
            Write-Warn2 "  注意：Python $($best.Major).$($best.Minor) 较新，部分 torch 版本可能没有对应 wheel；如装机失败请装 3.11 或 3.12 后重试。"
        }
        return $best.Exe
    }
    return ''
}

# ---- 2) 下载 + 强校验 + 解压免安装 Python ----
function Get-Curl {
    $c = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return ''
}

function Test-Archive([string]$path) {
    if (-not (Test-Path $path)) { return $false }
    $len = (Get-Item $path).Length
    if ($len -ne $pySize) {
        Write-Warn2 "  下载不完整（$len / $pySize 字节），将继续续传"
        return $false
    }
    $hash = (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLowerInvariant()
    if ($hash -ne $pySha) {
        Write-Warn2 "  哈希不符，丢弃该文件重新下载"
        Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
        return $false
    }
    return $true
}

function Download-Python {
    New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
    $curl = Get-Curl
    if (-not $curl) {
        Write-Bad '  没找到 curl.exe，无法自动下载 Python。'
        Write-Bad '  请用浏览器手动下载下面地址的文件，放到：'
        Write-Bad "  $downloadDir"
        Write-Bad "  $official"
        throw '缺少 curl.exe'
    }
    foreach ($prefix in $mirrorPrefixes) {
        $url = "$prefix$official"
        Write-Step "  下载源：$url"
        # -C - 断点续传：网断了重跑会接着下，不从头来。
        # 两个坑都踩过，这里取最稳写法：
        #   1) 直接显示进度条不行——curl 进度走 stderr，PowerShell 会包成
        #      NativeCommandError 刷一屏红色，用户以为装崩了；
        #   2) 改用独立进程启动也不行——同机同时存在 HTTPS_PROXY / https_proxy 时
        #      它会直接抛异常，下载彻底失败。
        # 所以：抑制进度条 + 丢弃 stderr，用明确的阶段提示让用户知道在动。
        Write-Host '  正在下载，请勿关闭窗口（约 49MB，视网速 1-5 分钟）...' -ForegroundColor DarkGray
        # 用 -sS 而不是 --no-progress-meter：后者是 curl 7.67+ 才有，
        # 老 Windows 10（1809 自带 7.55）会直接报错；-sS 从 7.x 早期就支持。
        & $curl -L -sS --fail --retry 3 --retry-delay 2 -C - -o $archive $url 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 "  该源返回错误（exit $LASTEXITCODE），换下一个"
            continue
        }
        if (Test-Archive $archive) { return }
    }
    Write-Bad '  所有下载源都失败。请手动下载并放到指定目录后重新双击：'
    Write-Bad "  $official"
    Write-Bad "  期望 SHA256：$pySha"
    throw 'Python 下载失败'
}

function Expand-Python {
    $exe = Join-Path $extractDir 'python\python.exe'
    if (Test-Path $exe) { return $exe }
    $tar = (Get-Command tar.exe -ErrorAction SilentlyContinue)
    if (-not $tar) {
        Write-Bad '  没找到 tar.exe（Windows 10 1803+ 自带）。请升级系统或用 7-Zip 手动解压：'
        Write-Bad "  $archive  ->  $extractDir"
        throw '缺少 tar.exe'
    }
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    Write-Step '  解压免安装 Python（约 150MB，稍候）...'
    & $tar.Source -xzf $archive -C $extractDir
    if ($LASTEXITCODE -ne 0) { throw '解压失败' }
    if (-not (Test-Path $exe)) { throw "解压后未找到 python.exe：$exe" }
    return $exe
}

# ---- 主流程 ----
Write-Host '============================================' -ForegroundColor Cyan
Write-Host '  JJOS 一键安装（第一次运行会下载一个免安装 Python）' -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan

$pythonExe = Resolve-SystemPython
if ($pythonExe) {
    Write-Ok "[1/3] 本机已有 Python：$pythonExe"
} else {
    Write-Step '[1/3] 本机没装 Python，正在自动准备免安装版...'
    Download-Python
    $pythonExe = Expand-Python
    Write-Ok "  免安装 Python 就绪：$pythonExe"
}

$entry = Join-Path $jjosDir 'bootstrap.py'
if (-not (Test-Path $entry)) { throw "找不到安装入口：$entry" }

Write-Step '[2/3] 正在准备 ComfyUI 与本机配置...'
$extraArgs = @()
if ($env:JJOS_DRY_RUN) { $extraArgs += '--dry-run' }
& $pythonExe $entry --python $pythonExe @extraArgs
if ($LASTEXITCODE -ne 0) {
    Write-Bad '[X] 安装编排失败。'
    $logPath = Join-Path $jjosDir 'logs\install.log'
    if (Test-Path $logPath) {
        Write-Bad "  这次安装的记录已保存在：$logPath"
        Write-Bad '  遇到问题把该文件发给技术支持即可定位。'
    }
    Write-Bad '  处理后重新双击「一键安装.bat」即可，已装好的部分不会重装。'
    exit 1
}

Write-Ok '[3/3] 安装完成！'
Write-Host ''
Write-Host '  下一步：双击本目录的「一键启动.bat」' -ForegroundColor Green
Write-Host '  浏览器会自动打开傻瓜操作台，缺的环境会在后台自动补好。' -ForegroundColor Green
Write-Host '  首次启动会下载 torch/CUDA 等组件（约 2-3GB），请耐心等进度条走完。' -ForegroundColor Yellow
Write-Host ''
Write-Host "  安装记录：$(Join-Path $jjosDir 'logs\install.log')" -ForegroundColor DarkGray
Write-Host ''
if (-not $env:JJOS_NO_PAUSE) { Read-Host '按回车键关闭' | Out-Null }
