param(
  [Parameter(Mandatory = $true)][ValidateSet('embed', 'move', 'exists')][string]$Action,
  [Parameter(Mandatory = $true)][string]$Title,
  [Int64]$Parent = 0,
  [int]$X = 0,
  [int]$Y = 0,
  [int]$W = 0,
  [int]$H = 0
)

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class ClevenWin {
  public const int GWL_STYLE = -16;
  public const int GWL_EXSTYLE = -20;
  public const uint WS_CHILD = 0x40000000;
  public const uint WS_POPUP = 0x80000000;
  public const uint WS_CAPTION = 0x00C00000;
  public const uint WS_THICKFRAME = 0x00040000;
  public const uint WS_BORDER = 0x00800000;
  public const uint WS_SYSMENU = 0x00080000;
  public const uint WS_MINIMIZEBOX = 0x00020000;
  public const uint WS_MAXIMIZEBOX = 0x00010000;
  public const uint WS_VISIBLE = 0x10000000;
  public const int SWP_NOZORDER = 0x0004;
  public const int SWP_NOACTIVATE = 0x0010;
  public const int SWP_FRAMECHANGED = 0x0020;
  public const int SWP_SHOWWINDOW = 0x0040;

  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

  [DllImport("user32.dll")]
  public static extern IntPtr SetParent(IntPtr hWndChild, IntPtr hWndNewParent);

  [DllImport("user32.dll")]
  public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

  [DllImport("user32.dll")]
  public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

  [DllImport("user32.dll")]
  public static extern int GetWindowLong(IntPtr hWnd, int nIndex);

  [DllImport("user32.dll")]
  public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);

  [DllImport("user32.dll")]
  public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

  [DllImport("user32.dll")]
  public static extern bool IsWindow(IntPtr hWnd);
}
"@

$hwnd = [ClevenWin]::FindWindow($null, $Title)
if ($hwnd -eq [IntPtr]::Zero -or -not [ClevenWin]::IsWindow($hwnd)) {
  if ($Action -eq 'exists') { Write-Output '0'; exit 0 }
  Write-Output 'missing'
  exit 1
}

if ($Action -eq 'exists') {
  Write-Output '1'
  exit 0
}

if ($Action -eq 'embed') {
  if ($Parent -eq 0) {
    Write-Output 'no-parent'
    exit 1
  }
  $parentPtr = [IntPtr]$Parent
  $style = [ClevenWin]::GetWindowLong($hwnd, [ClevenWin]::GWL_STYLE)
  $style = $style -bor [ClevenWin]::WS_CHILD -bor [ClevenWin]::WS_VISIBLE
  $style = $style -band (-bnot [ClevenWin]::WS_POPUP)
  $style = $style -band (-bnot [ClevenWin]::WS_CAPTION)
  $style = $style -band (-bnot [ClevenWin]::WS_THICKFRAME)
  $style = $style -band (-bnot [ClevenWin]::WS_BORDER)
  $style = $style -band (-bnot [ClevenWin]::WS_SYSMENU)
  $style = $style -band (-bnot [ClevenWin]::WS_MINIMIZEBOX)
  $style = $style -band (-bnot [ClevenWin]::WS_MAXIMIZEBOX)
  [void][ClevenWin]::SetWindowLong($hwnd, [ClevenWin]::GWL_STYLE, $style)
  [void][ClevenWin]::SetParent($hwnd, $parentPtr)
  [void][ClevenWin]::MoveWindow($hwnd, $X, $Y, $W, $H, $true)
  $flags = [ClevenWin]::SWP_NOZORDER -bor [ClevenWin]::SWP_NOACTIVATE -bor [ClevenWin]::SWP_FRAMECHANGED -bor [ClevenWin]::SWP_SHOWWINDOW
  [void][ClevenWin]::SetWindowPos($hwnd, [IntPtr]::Zero, $X, $Y, $W, $H, $flags)
  Write-Output 'ok'
  exit 0
}

if ($Action -eq 'move') {
  [void][ClevenWin]::MoveWindow($hwnd, $X, $Y, $W, $H, $true)
  Write-Output 'ok'
  exit 0
}
