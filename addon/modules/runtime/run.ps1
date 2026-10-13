param(
    [Parameter(Mandatory=$true)][string]$Script,
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$Operation
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$configData = Get-Content -Raw -Encoding UTF8 -LiteralPath $Config | ConvertFrom-Json
$python = $configData.runtime.python
$pythonArgs = @()
if ($python -isnot [string]) {
    if ($python.tool) {
        $toolDirectory = & $python.uv tool dir
        if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the configured uv tools directory' }
        $python = Join-Path $toolDirectory.Trim() (Join-Path $python.tool 'Scripts\python.exe')
    } else {
        $pythonArgs = @($python.args)
        $python = $python.program
    }
}
& $python @pythonArgs $Script --config $Config $Operation
exit $LASTEXITCODE
