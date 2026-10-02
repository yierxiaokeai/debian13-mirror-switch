$ErrorActionPreference = 'Stop'
$mirrorChoice = Read-Host "选择软件源：1 华为云优先，2 中科大优先，3 查看配置"
$selectedMirror = switch ($mirrorChoice) {
    '1' { 'huawei' }
    '2' { 'ustc' }
    '3' { 'status' }
    default { throw '请输入 1、2 或 3。' }
}
$helperPath = Join-Path $PSScriptRoot 'switch-mirror.py'
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) { throw '同目录缺少 switch-mirror.py。' }
$linuxPath = & wsl.exe -d Debian --exec wslpath -a $helperPath
if ($LASTEXITCODE -ne 0) { throw '无法转换脚本路径。' }
& wsl.exe -d Debian -u root --exec python3 $linuxPath.Trim() $selectedMirror
if ($LASTEXITCODE -ne 0) { throw '软件源切换未完成，请查看上面的错误和备份位置。' }
Read-Host '按回车关闭' | Out-Null
