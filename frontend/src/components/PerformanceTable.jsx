import React, { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUpDown, Inbox, Loader2 } from 'lucide-react';

const DATE_PRESETS = ['last_7d', 'last_30d', 'this_month', 'last_month'];

const buildDefaultColumns = (t) => [
    { key: 'name', label: t('columns.campaign'), numeric: false },
    { key: 'impressions', label: t('columns.impressions'), numeric: true },
    { key: 'clicks', label: t('columns.clicks'), numeric: true },
    { key: 'cost', label: t('columns.cost'), numeric: true, format: (v) => `$${Number(v).toFixed(2)}` },
    { key: 'conversions', label: t('columns.conversions'), numeric: true },
];

/**
 * Reusable campaign/ad performance table shared across Google Ads, TikTok
 * Ads, and the cross-platform Overview page. Callers own data-fetching (pass
 * rows + loading state) — this component only handles sorting, the
 * date-range filter dropdown, and empty/loading states. Never hardcode mock
 * rows here — see docs/sprint-0/done.md's note on Reporting.jsx's mock-data
 * anti-pattern.
 */
export default function PerformanceTable({
    rows = [],
    loading = false,
    columns: columnsProp,
    datePreset,
    onDatePresetChange,
    emptyMessage: emptyMessageProp,
    renderActions,
}) {
    const { t } = useTranslation('dashboard');
    const columns = columnsProp ?? buildDefaultColumns((key) => t(key));
    const emptyMessage = emptyMessageProp ?? t('performance.empty');
    const [sortKey, setSortKey] = useState(columns.find((c) => c.numeric)?.key ?? columns[0].key);
    const [sortDir, setSortDir] = useState('desc');

    const sortedRows = useMemo(() => {
        const copy = [...rows];
        copy.sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            if (typeof av === 'number' && typeof bv === 'number') {
                return sortDir === 'asc' ? av - bv : bv - av;
            }
            return sortDir === 'asc'
                ? String(av ?? '').localeCompare(String(bv ?? ''))
                : String(bv ?? '').localeCompare(String(av ?? ''));
        });
        return copy;
    }, [rows, sortKey, sortDir]);

    const handleSort = (key) => {
        if (key === sortKey) {
            setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
        } else {
            setSortKey(key);
            setSortDir('desc');
        }
    };

    return (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            {onDatePresetChange && (
                <div className="flex justify-end p-3 sm:p-4 border-b border-gray-100">
                    <select
                        value={datePreset}
                        onChange={(e) => onDatePresetChange(e.target.value)}
                        className="border border-gray-300 rounded-lg px-3 sm:px-4 py-2 text-sm bg-white focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                    >
                        {DATE_PRESETS.map((preset) => (
                            <option key={preset} value={preset}>
                                {t(`datePresets.${preset}`)}
                            </option>
                        ))}
                    </select>
                </div>
            )}

            {/* Horizontal scroll keeps wide metric tables usable on phones
                without squeezing columns into unreadability. */}
            <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[560px]">
                <thead className="bg-gray-50 text-gray-600">
                    <tr>
                        {columns.map((col) => (
                            <th
                                key={col.key}
                                className={`px-4 py-3 font-medium select-none cursor-pointer hover:text-amber-600 ${col.numeric ? 'text-right' : 'text-left'}`}
                                onClick={() => handleSort(col.key)}
                            >
                                <span className={`inline-flex items-center gap-1 ${col.numeric ? 'flex-row-reverse' : ''}`}>
                                    {col.label}
                                    <ArrowUpDown size={12} className={sortKey === col.key ? 'text-amber-600' : 'text-gray-300'} />
                                </span>
                            </th>
                        ))}
                        {renderActions && <th className="px-4 py-3 font-medium text-right">{t('performance.actions')}</th>}
                    </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                    {loading && (
                        <tr>
                            <td colSpan={columns.length + (renderActions ? 1 : 0)} className="px-4 py-10 text-center text-gray-400">
                                <Loader2 size={20} className="animate-spin inline-block mr-2" />
                                {t('performance.loading')}
                            </td>
                        </tr>
                    )}
                    {!loading && sortedRows.length === 0 && (
                        <tr>
                            <td colSpan={columns.length + (renderActions ? 1 : 0)} className="px-4 py-10 text-center text-gray-400">
                                <Inbox size={24} className="inline-block mb-2" />
                                <p>{emptyMessage}</p>
                            </td>
                        </tr>
                    )}
                    {!loading &&
                        sortedRows.map((row) => (
                            <tr key={row.id} className="hover:bg-amber-50/50">
                                {columns.map((col) => (
                                    <td key={col.key} className={`px-4 py-3 ${col.numeric ? 'text-right tabular-nums' : 'text-left text-gray-800'}`}>
                                        {col.format ? col.format(row[col.key]) : row[col.key]}
                                    </td>
                                ))}
                                {renderActions && (
                                    <td className="px-4 py-3 text-right">
                                        {renderActions(row)}
                                    </td>
                                )}
                            </tr>
                        ))}
                </tbody>
            </table>
            </div>
        </div>
    );
}
