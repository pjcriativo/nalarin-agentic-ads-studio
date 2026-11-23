"""initial schema baseline

This is now the sole root migration. The 16 migrations that previously formed
the history (2512a96c6e3c ... f4be6367be6c) assumed the base schema already
existed in the database (it was always created via Base.metadata.create_all()
in init_db.py, never via Alembic) — `alembic upgrade head` against a genuinely
fresh database always failed on the very first migration
(e9173c698de9, "ALTER TABLE generated_ads" — relation does not exist).
Railway's actual deploy path never ran `alembic upgrade head` either (its
startCommand overrides the Dockerfile's CMD and skips migrations entirely),
which is why this was never caught. Those 16 files are kept for reference only
in `backend/_superseded_migrations_reference/` (excluded from Alembic's scan
path) — see docs/sprint-0/done.md for the full explanation.

This migration creates the schema as it stood when the baseline was frozen
(commit bf488a3, before multi-tenancy) in one step, so `alembic upgrade head`
works against an empty database. IT IS A STATIC SNAPSHOT of DDL (every
statement is IF NOT EXISTS, so databases that were originally built with
create_all() are left untouched). It must never import app.models: it used to
call Base.metadata.create_all(), which silently pulled every later model change
into the baseline and made incremental migrations collide with it. Every schema
change belongs in a NEW migration on top of this one.

Revision ID: fba8f217905e
Revises:
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'fba8f217905e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen DDL (generated from the models of commit bf488a3, dependency order).
SCHEMA_DDL = (
    """CREATE TABLE IF NOT EXISTS ad_styles (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	category VARCHAR NOT NULL, 
	description TEXT, 
	best_for JSON, 
	visual_layout VARCHAR, 
	psychology TEXT, 
	mood VARCHAR, 
	lighting VARCHAR, 
	composition VARCHAR, 
	design_style VARCHAR, 
	prompt TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
)
    """,
    """CREATE TABLE IF NOT EXISTS api_usage_logs (
	id VARCHAR NOT NULL, 
	endpoint VARCHAR NOT NULL, 
	api_calls INTEGER NOT NULL, 
	ads_returned INTEGER NOT NULL, 
	ads_saved INTEGER NOT NULL, 
	query VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	date VARCHAR NOT NULL, 
	PRIMARY KEY (id)
)
    """,
    """CREATE INDEX IF NOT EXISTS ix_api_usage_logs_date ON api_usage_logs (date)
    """,
    """CREATE TABLE IF NOT EXISTS brand_scrapes (
	id VARCHAR NOT NULL, 
	brand_name VARCHAR NOT NULL, 
	page_id VARCHAR NOT NULL, 
	page_name VARCHAR, 
	page_url VARCHAR NOT NULL, 
	total_ads INTEGER, 
	media_downloaded INTEGER, 
	status VARCHAR, 
	error_message TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE INDEX IF NOT EXISTS ix_brand_scrapes_brand_name ON brand_scrapes (brand_name)
    """,
    """CREATE TABLE IF NOT EXISTS brands (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	logo VARCHAR, 
	primary_color VARCHAR, 
	secondary_color VARCHAR, 
	highlight_color VARCHAR, 
	voice TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE TABLE IF NOT EXISTS customer_profiles (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	demographics TEXT, 
	pain_points TEXT, 
	goals TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE TABLE IF NOT EXISTS facebook_campaigns (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	objective VARCHAR NOT NULL, 
	budget_type VARCHAR NOT NULL, 
	daily_budget INTEGER, 
	bid_strategy VARCHAR, 
	status VARCHAR, 
	fb_campaign_id VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE TABLE IF NOT EXISTS keyword_blacklist (
	id VARCHAR NOT NULL, 
	keyword VARCHAR NOT NULL, 
	reason VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_keyword_blacklist_keyword ON keyword_blacklist (keyword)
    """,
    """CREATE TABLE IF NOT EXISTS page_blacklist (
	id VARCHAR NOT NULL, 
	page_name VARCHAR NOT NULL, 
	reason VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_page_blacklist_page_name ON page_blacklist (page_name)
    """,
    """CREATE TABLE IF NOT EXISTS permissions (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	description TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (name)
)
    """,
    """CREATE TABLE IF NOT EXISTS prompts (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	category VARCHAR NOT NULL, 
	description TEXT, 
	variables JSON, 
	template TEXT NOT NULL, 
	notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
)
    """,
    """CREATE TABLE IF NOT EXISTS roles (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	description TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (name)
)
    """,
    """CREATE TABLE IF NOT EXISTS users (
	id VARCHAR NOT NULL, 
	email VARCHAR NOT NULL, 
	hashed_password VARCHAR NOT NULL, 
	name VARCHAR, 
	is_active BOOLEAN, 
	is_superuser BOOLEAN, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)
    """,
    """CREATE TABLE IF NOT EXISTS verticals (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	description TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_verticals_name ON verticals (name)
    """,
    """CREATE TABLE IF NOT EXISTS winning_ads (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	image_url VARCHAR NOT NULL, 
	notes TEXT, 
	tags TEXT, 
	analysis TEXT, 
	recreation_prompt TEXT, 
	topic VARCHAR, 
	mood VARCHAR, 
	subject_matter VARCHAR, 
	copy_analysis TEXT, 
	product_name VARCHAR, 
	category VARCHAR, 
	design_style VARCHAR, 
	filename VARCHAR, 
	structural_analysis TEXT, 
	layering TEXT, 
	template_structure JSON, 
	color_palette JSON, 
	typography_system JSON, 
	copy_patterns JSON, 
	visual_elements JSON, 
	template_category VARCHAR, 
	blueprint_json JSON, 
	blueprint_analyzed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id)
)
    """,
    """CREATE TABLE IF NOT EXISTS api_keys (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	key_hash VARCHAR NOT NULL, 
	scopes JSON NOT NULL, 
	created_by_user_id VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_api_keys_key_hash ON api_keys (key_hash)
    """,
    """CREATE TABLE IF NOT EXISTS brand_profiles (
	brand_id VARCHAR NOT NULL, 
	profile_id VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (brand_id, profile_id), 
	FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE CASCADE, 
	FOREIGN KEY(profile_id) REFERENCES customer_profiles (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS brand_scraped_ads (
	id VARCHAR NOT NULL, 
	brand_scrape_id VARCHAR NOT NULL, 
	external_id VARCHAR NOT NULL, 
	page_name VARCHAR, 
	page_link VARCHAR, 
	headline VARCHAR, 
	ad_copy TEXT, 
	cta_text VARCHAR, 
	media_type VARCHAR, 
	media_urls JSON, 
	original_media_urls JSON, 
	platforms JSON, 
	start_date VARCHAR, 
	ad_link VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(brand_scrape_id) REFERENCES brand_scrapes (id) ON DELETE CASCADE
)
    """,
    """CREATE INDEX IF NOT EXISTS ix_brand_scraped_ads_external_id ON brand_scraped_ads (external_id)
    """,
    """CREATE TABLE IF NOT EXISTS facebook_adsets (
	id VARCHAR NOT NULL, 
	campaign_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	optimization_goal VARCHAR NOT NULL, 
	daily_budget INTEGER, 
	bid_strategy VARCHAR, 
	bid_amount INTEGER, 
	targeting JSON, 
	pixel_id VARCHAR, 
	conversion_event VARCHAR, 
	status VARCHAR, 
	fb_adset_id VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(campaign_id) REFERENCES facebook_campaigns (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS facebook_pages (
	id VARCHAR NOT NULL, 
	page_name VARCHAR NOT NULL, 
	page_url VARCHAR, 
	vertical_id VARCHAR, 
	total_ads INTEGER, 
	first_seen TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	last_seen TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(vertical_id) REFERENCES verticals (id) ON DELETE SET NULL
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_facebook_pages_page_name ON facebook_pages (page_name)
    """,
    """CREATE TABLE IF NOT EXISTS google_ads_connections (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	customer_id VARCHAR NOT NULL, 
	account_name VARCHAR, 
	encrypted_refresh_token TEXT NOT NULL, 
	encrypted_access_token TEXT, 
	access_token_expires_at TIMESTAMP WITH TIME ZONE, 
	is_active BOOLEAN, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS meta_ads_connections (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	ad_account_id VARCHAR NOT NULL, 
	account_name VARCHAR, 
	encrypted_access_token TEXT NOT NULL, 
	access_token_expires_at TIMESTAMP WITH TIME ZONE, 
	is_active BOOLEAN, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS mutation_audits (
	id VARCHAR NOT NULL, 
	user_id VARCHAR, 
	provider VARCHAR NOT NULL, 
	account_id VARCHAR NOT NULL, 
	action VARCHAR NOT NULL, 
	campaign_id VARCHAR, 
	payload_hash VARCHAR NOT NULL, 
	mode VARCHAR NOT NULL, 
	verdict_reason VARCHAR, 
	executed BOOLEAN, 
	metrics JSON, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS products (
	id VARCHAR NOT NULL, 
	brand_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	description TEXT, 
	product_shots JSON, 
	default_url TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS refresh_tokens (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	token_hash VARCHAR NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_refresh_tokens_token_hash ON refresh_tokens (token_hash)
    """,
    """CREATE TABLE IF NOT EXISTS role_permissions (
	role_id VARCHAR NOT NULL, 
	permission_id VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (role_id, permission_id), 
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE, 
	FOREIGN KEY(permission_id) REFERENCES permissions (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS saved_searches (
	id VARCHAR NOT NULL, 
	query VARCHAR NOT NULL, 
	country VARCHAR, 
	negative_keywords JSON, 
	vertical_id VARCHAR, 
	search_type VARCHAR, 
	schedule_config JSON, 
	is_active BOOLEAN, 
	last_run TIMESTAMP WITH TIME ZONE, 
	ads_requested INTEGER, 
	ads_returned INTEGER, 
	ads_new INTEGER, 
	ads_duplicate INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(vertical_id) REFERENCES verticals (id) ON DELETE SET NULL
)
    """,
    """CREATE TABLE IF NOT EXISTS search_logs (
	id VARCHAR NOT NULL, 
	search_query VARCHAR NOT NULL, 
	country VARCHAR, 
	negative_keywords JSON, 
	vertical_id VARCHAR, 
	total_ads_found INTEGER, 
	filtered_by_page_blacklist INTEGER, 
	filtered_by_keyword_blacklist INTEGER, 
	final_ads_saved INTEGER, 
	new_pages_blacklisted JSON, 
	api_calls_made INTEGER, 
	search_type VARCHAR, 
	execution_time_seconds INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	date VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(vertical_id) REFERENCES verticals (id) ON DELETE SET NULL
)
    """,
    """CREATE INDEX IF NOT EXISTS ix_search_logs_date ON search_logs (date)
    """,
    """CREATE TABLE IF NOT EXISTS tiktok_ads_connections (
	id VARCHAR NOT NULL, 
	user_id VARCHAR NOT NULL, 
	advertiser_id VARCHAR NOT NULL, 
	account_name VARCHAR, 
	encrypted_refresh_token TEXT NOT NULL, 
	encrypted_access_token TEXT, 
	access_token_expires_at TIMESTAMP WITH TIME ZONE, 
	is_active BOOLEAN, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS user_roles (
	user_id VARCHAR NOT NULL, 
	role_id VARCHAR NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (user_id, role_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS facebook_ads (
	id VARCHAR NOT NULL, 
	adset_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	creative_name VARCHAR, 
	image_url VARCHAR, 
	media_type VARCHAR, 
	video_url VARCHAR, 
	video_id VARCHAR, 
	thumbnail_url VARCHAR, 
	bodies JSON, 
	headlines JSON, 
	description TEXT, 
	cta VARCHAR, 
	website_url VARCHAR, 
	status VARCHAR, 
	fb_ad_id VARCHAR, 
	fb_creative_id VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(adset_id) REFERENCES facebook_adsets (id) ON DELETE CASCADE
)
    """,
    """CREATE TABLE IF NOT EXISTS generated_ads (
	id VARCHAR NOT NULL, 
	brand_id VARCHAR, 
	product_id VARCHAR, 
	template_id VARCHAR, 
	image_url VARCHAR, 
	headline VARCHAR, 
	body TEXT, 
	cta VARCHAR, 
	size_name VARCHAR, 
	dimensions VARCHAR, 
	prompt TEXT, 
	ad_bundle_id VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	media_type VARCHAR, 
	video_url VARCHAR, 
	video_id VARCHAR, 
	thumbnail_url VARCHAR, 
	PRIMARY KEY (id), 
	FOREIGN KEY(brand_id) REFERENCES brands (id) ON DELETE SET NULL, 
	FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE SET NULL, 
	FOREIGN KEY(template_id) REFERENCES winning_ads (id) ON DELETE SET NULL
)
    """,
    """CREATE TABLE IF NOT EXISTS scraped_ads (
	id VARCHAR NOT NULL, 
	brand_name VARCHAR, 
	headline VARCHAR, 
	ad_copy TEXT, 
	cta_text VARCHAR, 
	platform VARCHAR, 
	external_id VARCHAR, 
	content_hash VARCHAR, 
	ad_link VARCHAR NOT NULL, 
	platforms JSON, 
	start_date VARCHAR, 
	media_type VARCHAR, 
	first_seen TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	last_seen TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	seen_count INTEGER, 
	search_id VARCHAR, 
	facebook_page_id VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	PRIMARY KEY (id), 
	FOREIGN KEY(search_id) REFERENCES saved_searches (id) ON DELETE CASCADE, 
	FOREIGN KEY(facebook_page_id) REFERENCES facebook_pages (id) ON DELETE SET NULL
)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_scraped_ads_content_hash ON scraped_ads (content_hash)
    """,
    """CREATE UNIQUE INDEX IF NOT EXISTS ix_scraped_ads_external_id ON scraped_ads (external_id)
    """,
)

TABLES = ('ad_styles', 'api_usage_logs', 'brand_scrapes', 'brands', 'customer_profiles', 'facebook_campaigns', 'keyword_blacklist', 'page_blacklist', 'permissions', 'prompts', 'roles', 'users', 'verticals', 'winning_ads', 'api_keys', 'brand_profiles', 'brand_scraped_ads', 'facebook_adsets', 'facebook_pages', 'google_ads_connections', 'meta_ads_connections', 'mutation_audits', 'products', 'refresh_tokens', 'role_permissions', 'saved_searches', 'search_logs', 'tiktok_ads_connections', 'user_roles', 'facebook_ads', 'generated_ads', 'scraped_ads')


def upgrade() -> None:
    """Create the baseline schema (idempotent)."""
    bind = op.get_bind()
    for statement in SCHEMA_DDL:
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    """Drop the baseline schema."""
    bind = op.get_bind()
    for table in reversed(TABLES):
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}" CASCADE')
