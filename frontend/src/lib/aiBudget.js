/** Presentation rules for spending limits (pure: the server enforces the limit, this only explains it). */
export const WARN_AT = 0.8;

/**
 * state: off (not enforced) | ok | warning (>= 80% of the limit used or in flight) | reached (no room left).
 * `used` counts what is spent AND what is reserved by requests still running.
 */
export function budgetState(budget) {
    const limit = Number(budget.limit_usd) || 0;
    const used = (Number(budget.spent_usd) || 0) + (Number(budget.reserved_usd) || 0);
    const ratio = limit > 0 ? used / limit : 1; // a limit of 0 blocks everything
    const percent = Math.min(100, Math.floor(ratio * 100));
    if (!budget.enabled) return { state: 'off', percent, ratio };
    if (ratio >= 1) return { state: 'reached', percent: 100, ratio };
    if (ratio >= WARN_AT) return { state: 'warning', percent, ratio };
    return { state: 'ok', percent, ratio };
}

/** The most urgent enforced budget, for the page-level banner (reached beats warning; monthly/daily by order). */
export function worstBudget(budgets) {
    let worst = null;
    for (const budget of budgets) {
        const info = budgetState(budget);
        if (info.state !== 'reached' && info.state !== 'warning') continue;
        if (!worst || (info.state === 'reached' && worst.info.state !== 'reached')) worst = { budget, info };
    }
    return worst;
}
