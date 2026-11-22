import React from 'react';
import { BarChart3, ExternalLink, LockKeyhole } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '../components/LanguageSwitcher';
import { APP_NAME, APP_LOGO, APP_OPERATOR, APP_OPERATOR_URL } from '../lib/branding';

export default function PublicInfoPage({ page }) {
    const { t } = useTranslation('public');
    const vars = { appName: APP_NAME, operator: APP_OPERATOR };
    const data = {
        eyebrow: t(`${page}.eyebrow`, vars),
        title: page === 'about' ? APP_NAME : t(`${page}.title`, vars),
        summary: t(`${page}.summary`, vars),
        content: t(`${page}.content`, { ...vars, returnObjects: true }),
    };

    return (
        <div className="min-h-screen bg-[#FFFAF0] text-gray-900">
            <header className="border-b border-amber-200 bg-white">
                <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-4">
                    <Link to="/about" className="flex items-center gap-3">
                        <img src={APP_LOGO} alt={APP_NAME} className="h-10 w-10" />
                        <div>
                            <div className="font-bold text-amber-950">{APP_NAME}</div>
                            <div className="text-xs text-amber-700">{t('operatedBy', vars)}</div>
                        </div>
                    </Link>
                    <div className="flex items-center gap-4">
                        <LanguageSwitcher />
                        <Link to="/login" className="inline-flex items-center gap-2 text-sm font-semibold text-amber-800 hover:text-amber-950">
                            {t('operatorSignIn')} <ExternalLink size={15} />
                        </Link>
                    </div>
                </div>
            </header>

            <main>
                <section className="border-b border-amber-200 bg-amber-50">
                    <div className="mx-auto max-w-5xl px-5 py-12 sm:py-16">
                        <div className="mb-5 flex h-12 w-12 items-center justify-center bg-amber-700 text-white">
                            {page === 'privacy' ? <LockKeyhole size={24} /> : <BarChart3 size={24} />}
                        </div>
                        <p className="mb-2 text-sm font-bold uppercase text-amber-700">{data.eyebrow}</p>
                        <h1 className="max-w-3xl text-4xl font-bold text-amber-950 sm:text-5xl">{data.title}</h1>
                        <p className="mt-5 max-w-3xl text-lg leading-8 text-gray-700">{data.summary}</p>
                        {page !== 'about' && <p className="mt-4 text-sm text-gray-500">{t('updated', { date: t('updatedAt') })}</p>}
                    </div>
                </section>

                <section className="mx-auto grid max-w-5xl gap-x-12 gap-y-10 px-5 py-12 md:grid-cols-2">
                    {data.content.map(({ heading, body }) => (
                        <article key={heading}>
                            <h2 className="text-lg font-bold text-amber-950">{heading}</h2>
                            <p className="mt-3 leading-7 text-gray-700">{body}</p>
                        </article>
                    ))}
                </section>
            </main>

            <footer className="border-t border-amber-200 bg-white">
                <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-3 px-5 py-6 text-sm text-gray-600">
                    <span>© 2026 {APP_OPERATOR}</span>
                    <Link to="/about" className="hover:text-amber-800">{t('nav.about')}</Link>
                    <Link to="/privacy" className="hover:text-amber-800">{t('nav.privacy')}</Link>
                    <Link to="/terms" className="hover:text-amber-800">{t('nav.terms')}</Link>
                    <a href={APP_OPERATOR_URL} target="_blank" rel="noreferrer" className="hover:text-amber-800">{APP_OPERATOR}</a>
                </div>
            </footer>
        </div>
    );
}