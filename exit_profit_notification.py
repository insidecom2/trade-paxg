from exit_profit import ExitProfitCheck


def should_send_exit_profit_notification(check: ExitProfitCheck) -> bool:
    """Send an exit-profit notification only when both closes share a zone."""
    return check.same_zone


def build_exit_profit_notification(
    symbol: str, check: ExitProfitCheck
) -> str:
    """Build the standalone Telegram message for the 15m exit-profit check."""
    message = [
        "PAXG 15m Exit Profit Check",
        f"Symbol: {symbol}",
    ]
    if check.close_1 is None or check.close_2 is None:
        message.extend([
            "Close #1: N/A",
            "Close #2: N/A",
            "Same Zone: NO",
            "Exit Profit Alert: NONE",
        ])
        return "\n".join(message)

    message.extend([
        "",
        "Candle #1 (15m)",
        f"Close #1: ${check.close_1:.2f}",
        f"High #1 : {check.high_1:.1f}",
        f"Volume #1: {check.volume_1:.2f}",
        "",
        "Candle #2 (15m)",
        f"Close #2: ${check.close_2:.2f}",
        f"High #2 : {check.high_2:.1f}",
        f"Volume #2: {check.volume_2:.2f}",
        f"ATR Zone: ±{check.zone_tolerance:.2%}",
        f"Same Zone: {'YES' if check.same_zone else 'NO'}",
        "",
        f"Touch Count : {check.touch_count}",
        "",
        "Resistance Zone",
        f"{check.resistance_zone_low:.1f} - {check.resistance_zone_high:.1f}",
        "",
        f"Volume Change : {check.volume_change_percent:.1f}%"
        if check.volume_change_percent is not None
        else "Volume Change : N/A",
        "",
        "Momentum",
        "RSI",
        f"Previous : {check.rsi_previous:.0f}"
        if check.rsi_previous is not None
        else "Previous : N/A",
        f"Current : {check.rsi_current:.0f}"
        if check.rsi_current is not None
        else "Current : N/A",
        f"Momentum : {check.momentum}",
    ])
    if check.exit_price is not None:
        message.append(f"Exit Profit Alert: ${check.exit_price:.2f}")
    else:
        message.append("Exit Profit Alert: NONE")
    return "\n".join(message)
