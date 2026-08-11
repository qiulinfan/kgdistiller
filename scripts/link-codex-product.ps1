[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

& kgdistiller codex link @Arguments
exit $LASTEXITCODE
