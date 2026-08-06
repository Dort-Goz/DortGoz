[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Output,
    [string[]]$Image
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Arguments = @("$Root/scripts/offline_bundle.py", "--output", $Output)
foreach ($tag in $Image) {
    $Arguments += @("--image", $tag)
}

& python @Arguments
exit $LASTEXITCODE
