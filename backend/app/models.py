from sqlalchemy import (
    Column, String, Integer, BigInteger, ForeignKey, DateTime, Text, JSON, Table, Boolean,
    UniqueConstraint, ForeignKeyConstraint, CheckConstraint, Index, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import uuid

def generate_uuid():
    return str(uuid.uuid4())

# Many-to-Many relationship table for User <-> Role
user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', String, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', String, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    Column('created_at', DateTime(timezone=True), server_default=func.now())
)

# Many-to-Many relationship table for Role <-> Permission
role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id', String, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    Column('permission_id', String, ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True),
    Column('created_at', DateTime(timezone=True), server_default=func.now())
)

# Many-to-Many relationship table for Brand <-> CustomerProfile
# organization_id is filled by a BEFORE INSERT trigger from the brand and checked
# by composite foreign keys, so a link between a brand and a profile of two
# different organizations is impossible at the database level.
brand_profiles = Table(
    'brand_profiles',
    Base.metadata,
    Column('brand_id', String, ForeignKey('brands.id', ondelete='CASCADE'), primary_key=True),
    Column('profile_id', String, ForeignKey('customer_profiles.id', ondelete='CASCADE'), primary_key=True),
    Column('created_at', DateTime(timezone=True), server_default=func.now()),
    Column('organization_id', String, ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=True),
    ForeignKeyConstraint(['brand_id', 'organization_id'], ['brands.id', 'brands.organization_id'],
                         name='fk_brand_profiles_brand_org', ondelete='CASCADE'),
    ForeignKeyConstraint(['profile_id', 'organization_id'], ['customer_profiles.id', 'customer_profiles.organization_id'],
                         name='fk_brand_profiles_profile_org', ondelete='CASCADE'),
    Index('ix_brand_profiles_organization_id', 'organization_id'),
)

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    roles = relationship("Role", secondary=user_roles, back_populates="users")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")

    def has_permission(self, permission_name: str) -> bool:
        """Check if user has a specific permission through any of their roles"""
        if self.is_superuser:
            return True
        for role in self.roles:
            for permission in role.permissions:
                if permission.name == permission_name:
                    return True
        return False

    def has_role(self, role_name: str) -> bool:
        """Check if user has a specific role"""
        if self.is_superuser:
            return True
        return any(role.name == role_name for role in self.roles)

class Role(Base):
    __tablename__ = "roles"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", secondary=user_roles, back_populates="roles")
    permissions = relationship("Permission", secondary=role_permissions, back_populates="roles")

class Permission(Base):
    __tablename__ = "permissions"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, nullable=False)  # e.g., "brands:create", "ads:delete"
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    roles = relationship("Role", secondary=role_permissions, back_populates="permissions")

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # SHA-256 hash of the raw token — the raw value only ever lives in the
    # client's storage, so a database read cannot mint a valid session.
    token_hash = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="refresh_tokens")

class Organization(Base):
    """A tenant. All private business data hangs off an organization."""
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    members = relationship("OrganizationMember", back_populates="organization", cascade="all, delete-orphan")


class OrganizationMember(Base):
    """A user's membership (and RBAC role) inside one organization.

    A membership is never deleted when access is withdrawn: it is revoked
    (status='revoked') so there is an audit trail and re-adding is explicit.
    """
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint('organization_id', 'user_id', name='uq_org_members_org_user'),
        CheckConstraint("role IN ('owner','admin','manager','analyst','viewer')", name='ck_org_members_role'),
        CheckConstraint("status IN ('active','revoked')", name='ck_org_members_status'),
        Index('ix_org_members_user_id', 'user_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active", server_default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", back_populates="members")
    user = relationship("User")


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_brands_id_org'),
        Index('ix_brands_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner. NULL only for LEGACY rows created before multi-tenancy:
    # they are invisible to every organization (RLS) until an operator claims
    # them explicitly (app/scripts/claim_legacy_data.py).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    name = Column(String, nullable=False)
    logo = Column(String, nullable=True)
    primary_color = Column(String, default='#3B82F6')
    secondary_color = Column(String, default='#10B981')
    highlight_color = Column(String, default='#F59E0B')
    voice = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # The composite (brand_id, organization_id) foreign keys add a second FK
    # path between these tables, so every join is spelled out explicitly.
    products = relationship("Product", back_populates="brand", cascade="all, delete-orphan",
                            primaryjoin="Brand.id == Product.brand_id", foreign_keys="Product.brand_id")
    profiles = relationship("CustomerProfile", secondary=brand_profiles, back_populates="brands",
                            primaryjoin="Brand.id == brand_profiles.c.brand_id",
                            secondaryjoin="CustomerProfile.id == brand_profiles.c.profile_id")
    generated_ads = relationship("GeneratedAd", back_populates="brand",
                                 primaryjoin="Brand.id == GeneratedAd.brand_id", foreign_keys="GeneratedAd.brand_id")

    @property
    def colors(self):
        return {
            "primary": self.primary_color,
            "secondary": self.secondary_color,
            "highlight": self.highlight_color
        }
    
    @property
    def profileIds(self):
        return [p.id for p in self.profiles]

class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        ForeignKeyConstraint(['brand_id', 'organization_id'], ['brands.id', 'brands.organization_id'],
                             name='fk_products_brand_org', ondelete='CASCADE'),
        UniqueConstraint('id', 'organization_id', name='uq_products_id_org'),
        Index('ix_products_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    brand_id = Column(String, ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    product_shots = Column(JSON, nullable=True)
    default_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    brand = relationship("Brand", back_populates="products",
                         primaryjoin="Brand.id == Product.brand_id", foreign_keys="Product.brand_id")

class CustomerProfile(Base):
    __tablename__ = "customer_profiles"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_customer_profiles_id_org'),
        Index('ix_customer_profiles_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    name = Column(String, nullable=False)
    demographics = Column(Text, nullable=True)
    pain_points = Column(Text, nullable=True)
    goals = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    brands = relationship("Brand", secondary=brand_profiles, back_populates="profiles",
                          primaryjoin="CustomerProfile.id == brand_profiles.c.profile_id",
                          secondaryjoin="Brand.id == brand_profiles.c.brand_id")

class FacebookCampaign(Base):
    __tablename__ = "facebook_campaigns"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_facebook_campaigns_id_org'),
        Index('ix_facebook_campaigns_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    name = Column(String, nullable=False)
    objective = Column(String, nullable=False)
    budget_type = Column(String, nullable=False)
    daily_budget = Column(Integer, nullable=True)
    bid_strategy = Column(String, nullable=True)
    status = Column(String, default='PAUSED')
    fb_campaign_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    adsets = relationship("FacebookAdSet", back_populates="campaign", cascade="all, delete-orphan",
                          primaryjoin="FacebookCampaign.id == FacebookAdSet.campaign_id", foreign_keys="FacebookAdSet.campaign_id")

class FacebookAdSet(Base):
    __tablename__ = "facebook_adsets"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_facebook_adsets_id_org'),
        ForeignKeyConstraint(['campaign_id', 'organization_id'], ['facebook_campaigns.id', 'facebook_campaigns.organization_id'],
                             name='fk_facebook_adsets_campaign_org', ondelete='CASCADE'),
        Index('ix_facebook_adsets_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    campaign_id = Column(String, ForeignKey("facebook_campaigns.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    optimization_goal = Column(String, nullable=False)
    daily_budget = Column(Integer, nullable=True)
    bid_strategy = Column(String, nullable=True)
    bid_amount = Column(Integer, nullable=True)
    targeting = Column(JSON, nullable=True)
    pixel_id = Column(String, nullable=True)
    conversion_event = Column(String, nullable=True)
    status = Column(String, default='PAUSED')
    fb_adset_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("FacebookCampaign", back_populates="adsets",
                            primaryjoin="FacebookCampaign.id == FacebookAdSet.campaign_id", foreign_keys="FacebookAdSet.campaign_id")
    ads = relationship("FacebookAd", back_populates="adset", cascade="all, delete-orphan",
                       primaryjoin="FacebookAdSet.id == FacebookAd.adset_id", foreign_keys="FacebookAd.adset_id")

class FacebookAd(Base):
    __tablename__ = "facebook_ads"
    __table_args__ = (
        ForeignKeyConstraint(['adset_id', 'organization_id'], ['facebook_adsets.id', 'facebook_adsets.organization_id'],
                             name='fk_facebook_ads_adset_org', ondelete='CASCADE'),
        Index('ix_facebook_ads_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    adset_id = Column(String, ForeignKey("facebook_adsets.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    creative_name = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    # Video support fields
    media_type = Column(String, default='image')  # 'image' or 'video'
    video_url = Column(String, nullable=True)
    video_id = Column(String, nullable=True)  # Facebook video ID
    thumbnail_url = Column(String, nullable=True)
    bodies = Column(JSON, nullable=True)
    headlines = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    cta = Column(String, nullable=True)
    website_url = Column(String, nullable=True)
    status = Column(String, default='PAUSED')
    fb_ad_id = Column(String, nullable=True)
    fb_creative_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    adset = relationship("FacebookAdSet", back_populates="ads",
                         primaryjoin="FacebookAdSet.id == FacebookAd.adset_id", foreign_keys="FacebookAd.adset_id")

class WinningAd(Base):
    __tablename__ = "winning_ads"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_winning_ads_id_org'),
        Index('ix_winning_ads_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    name = Column(String, nullable=False)
    image_url = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    analysis = Column(Text, nullable=True)
    recreation_prompt = Column(Text, nullable=True)
    topic = Column(String, nullable=True)
    mood = Column(String, nullable=True)
    subject_matter = Column(String, nullable=True)
    copy_analysis = Column(Text, nullable=True)
    product_name = Column(String, nullable=True)
    category = Column(String, nullable=True)
    design_style = Column(String, nullable=True)
    filename = Column(String, nullable=True)
    structural_analysis = Column(Text, nullable=True)
    layering = Column(Text, nullable=True)
    template_structure = Column(JSON, nullable=True)
    color_palette = Column(JSON, nullable=True)
    typography_system = Column(JSON, nullable=True)
    copy_patterns = Column(JSON, nullable=True)
    visual_elements = Column(JSON, nullable=True)
    template_category = Column(String, nullable=True)
    
    # Ad Remix Engine fields
    blueprint_json = Column(JSON, nullable=True)  # Stores the deconstructed blueprint
    blueprint_analyzed_at = Column(DateTime(timezone=True), nullable=True)  # When blueprint was created
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    generated_ads = relationship("GeneratedAd", back_populates="template",
                                 primaryjoin="WinningAd.id == GeneratedAd.template_id", foreign_keys="GeneratedAd.template_id")

class GeneratedAd(Base):
    __tablename__ = "generated_ads"
    __table_args__ = (
        ForeignKeyConstraint(['brand_id', 'organization_id'], ['brands.id', 'brands.organization_id'],
                             name='fk_generated_ads_brand_org', ondelete='SET NULL (brand_id)'),
        ForeignKeyConstraint(['product_id', 'organization_id'], ['products.id', 'products.organization_id'],
                             name='fk_generated_ads_product_org', ondelete='SET NULL (product_id)'),
        ForeignKeyConstraint(['template_id', 'organization_id'], ['winning_ads.id', 'winning_ads.organization_id'],
                             name='fk_generated_ads_template_org', ondelete='SET NULL (template_id)'),
        Index('ix_generated_ads_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    brand_id = Column(String, ForeignKey("brands.id", ondelete="SET NULL"), nullable=True)
    product_id = Column(String, ForeignKey("products.id", ondelete="SET NULL"), nullable=True) # Assuming product_id is also FK, though not explicit in original schema it makes sense
    template_id = Column(String, ForeignKey("winning_ads.id", ondelete="SET NULL"), nullable=True)
    image_url = Column(String, nullable=True)  # Changed to nullable for video ads
    headline = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    cta = Column(String, nullable=True)
    size_name = Column(String, nullable=True)
    dimensions = Column(String, nullable=True)
    prompt = Column(Text, nullable=True)
    ad_bundle_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Video support fields
    media_type = Column(String, default='image')  # 'image' or 'video'
    video_url = Column(String, nullable=True)
    video_id = Column(String, nullable=True)  # Facebook video ID
    thumbnail_url = Column(String, nullable=True)

    brand = relationship("Brand", back_populates="generated_ads",
                         primaryjoin="Brand.id == GeneratedAd.brand_id", foreign_keys="GeneratedAd.brand_id")
    template = relationship("WinningAd", back_populates="generated_ads",
                            primaryjoin="WinningAd.id == GeneratedAd.template_id", foreign_keys="GeneratedAd.template_id")

class Vertical(Base):
    __tablename__ = "verticals"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_verticals_id_org'),
        UniqueConstraint('organization_id', 'name', name='uq_verticals_org_name'),
        Index('ix_verticals_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    name = Column(String, nullable=False, index=True)  # e.g., "Legal", "Fitness", "E-commerce"
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    saved_searches = relationship("SavedSearch", back_populates="vertical",
                                  primaryjoin="Vertical.id == SavedSearch.vertical_id", foreign_keys="SavedSearch.vertical_id")


class FacebookPage(Base):
    __tablename__ = "facebook_pages"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_facebook_pages_id_org'),
        UniqueConstraint('organization_id', 'page_name', name='uq_facebook_pages_org_page_name'),
        ForeignKeyConstraint(['vertical_id', 'organization_id'], ['verticals.id', 'verticals.organization_id'],
                             name='fk_facebook_pages_vertical_org', ondelete='SET NULL (vertical_id)'),
        Index('ix_facebook_pages_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    page_name = Column(String, nullable=False, index=True)
    page_url = Column(String, nullable=True)
    vertical_id = Column(String, ForeignKey('verticals.id', ondelete='SET NULL'), nullable=True)
    total_ads = Column(Integer, default=0)  # Cached count of ads from this page
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vertical = relationship("Vertical", primaryjoin="Vertical.id == FacebookPage.vertical_id", foreign_keys="FacebookPage.vertical_id")
    ads = relationship("ScrapedAd", back_populates="facebook_page",
                       primaryjoin="FacebookPage.id == ScrapedAd.facebook_page_id", foreign_keys="ScrapedAd.facebook_page_id")


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_saved_searches_id_org'),
        ForeignKeyConstraint(['vertical_id', 'organization_id'], ['verticals.id', 'verticals.organization_id'],
                             name='fk_saved_searches_vertical_org', ondelete='SET NULL (vertical_id)'),
        Index('ix_saved_searches_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    query = Column(String, nullable=False)
    country = Column(String, nullable=True)
    negative_keywords = Column(JSON, nullable=True)  # List of negative keywords
    vertical_id = Column(String, ForeignKey('verticals.id', ondelete='SET NULL'), nullable=True)
    search_type = Column(String, default='one_time')  # 'one_time', 'scheduled_daily', 'scheduled_weekly'
    schedule_config = Column(JSON, nullable=True)  # Cron schedule config for scheduled searches
    is_active = Column(Boolean, default=True)  # For scheduled searches
    last_run = Column(DateTime(timezone=True), nullable=True)
    ads_requested = Column(Integer, nullable=True)  # How many ads were requested (limit)
    ads_returned = Column(Integer, nullable=True)  # How many ads API returned
    ads_new = Column(Integer, nullable=True)  # How many new ads (not duplicates)
    ads_duplicate = Column(Integer, nullable=True)  # How many duplicate ads
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vertical = relationship("Vertical", back_populates="saved_searches",
                            primaryjoin="Vertical.id == SavedSearch.vertical_id", foreign_keys="SavedSearch.vertical_id")
    ads = relationship("ScrapedAd", back_populates="saved_search", cascade="all, delete-orphan",
                       primaryjoin="SavedSearch.id == ScrapedAd.search_id", foreign_keys="ScrapedAd.search_id")


class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"
    __table_args__ = (
        Index('ix_api_usage_logs_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    endpoint = Column(String, nullable=False)  # "facebook_ads_library"
    api_calls = Column(Integer, nullable=False)  # Number of API calls made
    ads_returned = Column(Integer, nullable=False)  # Ads returned from API
    ads_saved = Column(Integer, nullable=False)  # Ads saved after filtering
    query = Column(String, nullable=True)  # Search query
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    date = Column(String, nullable=False, index=True)  # YYYY-MM-DD for daily grouping


class PageBlacklist(Base):
    __tablename__ = "page_blacklist"
    __table_args__ = (
        UniqueConstraint('organization_id', 'page_name', name='uq_page_blacklist_org_page_name'),
        Index('ix_page_blacklist_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    page_name = Column(String, nullable=False, index=True)  # Facebook page name
    reason = Column(String, nullable=True)  # Optional reason for blacklisting
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KeywordBlacklist(Base):
    __tablename__ = "keyword_blacklist"
    __table_args__ = (
        UniqueConstraint('organization_id', 'keyword', name='uq_keyword_blacklist_org_keyword'),
        Index('ix_keyword_blacklist_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    keyword = Column(String, nullable=False, index=True)  # Keyword to filter
    reason = Column(String, nullable=True)  # Optional reason for blacklisting
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class SearchLog(Base):
    __tablename__ = "search_logs"
    __table_args__ = (
        ForeignKeyConstraint(['vertical_id', 'organization_id'], ['verticals.id', 'verticals.organization_id'],
                             name='fk_search_logs_vertical_org', ondelete='SET NULL (vertical_id)'),
        Index('ix_search_logs_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    search_query = Column(String, nullable=False)
    country = Column(String, nullable=True)
    negative_keywords = Column(JSON, nullable=True)  # List of keywords excluded
    vertical_id = Column(String, ForeignKey('verticals.id', ondelete='SET NULL'), nullable=True)

    # Metrics
    total_ads_found = Column(Integer, default=0)  # Total ads returned from API
    filtered_by_page_blacklist = Column(Integer, default=0)  # Ads filtered by page blacklist
    filtered_by_keyword_blacklist = Column(Integer, default=0)  # Ads filtered by keyword blacklist
    final_ads_saved = Column(Integer, default=0)  # Final count after all filtering

    # New pages discovered
    new_pages_blacklisted = Column(JSON, nullable=True)  # List of page names added to blacklist during/after search

    # Execution details
    api_calls_made = Column(Integer, default=0)
    search_type = Column(String, nullable=True)  # 'one_time', 'scheduled_daily', 'scheduled_weekly'
    execution_time_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    date = Column(String, nullable=False, index=True)  # YYYY-MM-DD for daily grouping

    vertical = relationship("Vertical", primaryjoin="Vertical.id == SearchLog.vertical_id", foreign_keys="SearchLog.vertical_id")


class ScrapedAd(Base):
    __tablename__ = "scraped_ads"
    __table_args__ = (
        UniqueConstraint('organization_id', 'external_id', name='uq_scraped_ads_org_external_id'),
        UniqueConstraint('organization_id', 'content_hash', name='uq_scraped_ads_org_content_hash'),
        ForeignKeyConstraint(['search_id', 'organization_id'], ['saved_searches.id', 'saved_searches.organization_id'],
                             name='fk_scraped_ads_search_org', ondelete='CASCADE'),
        ForeignKeyConstraint(['facebook_page_id', 'organization_id'], ['facebook_pages.id', 'facebook_pages.organization_id'],
                             name='fk_scraped_ads_page_org', ondelete='SET NULL (facebook_page_id)'),
        Index('ix_scraped_ads_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    brand_name = Column(String, nullable=True)  # DEPRECATED: Use facebook_page relationship instead
    headline = Column(String, nullable=True)  # Ad headline
    ad_copy = Column(Text, nullable=True)  # Ad body text
    cta_text = Column(String, nullable=True)
    platform = Column(String, default='facebook')
    external_id = Column(String, nullable=True, index=True)  # ID from platform
    content_hash = Column(String, nullable=True, index=True)  # Hash of ad content for deduplication
    ad_link = Column(String, nullable=False)  # Link to original ad on FB Ads Library
    platforms = Column(JSON, nullable=True)  # ['facebook', 'instagram'] etc
    start_date = Column(String, nullable=True)  # When ad started running
    media_type = Column(String, nullable=True)  # 'image', 'video', or 'carousel'
    first_seen = Column(DateTime(timezone=True), server_default=func.now())  # First time ad was scraped
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # Last time ad was seen
    seen_count = Column(Integer, default=1)  # Number of times this ad has been encountered in scrapes
    search_id = Column(String, ForeignKey('saved_searches.id', ondelete='CASCADE'), nullable=True)  # Link to search
    facebook_page_id = Column(String, ForeignKey('facebook_pages.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    saved_search = relationship("SavedSearch", back_populates="ads",
                                primaryjoin="SavedSearch.id == ScrapedAd.search_id", foreign_keys="ScrapedAd.search_id")
    facebook_page = relationship("FacebookPage", back_populates="ads",
                                 primaryjoin="FacebookPage.id == ScrapedAd.facebook_page_id", foreign_keys="ScrapedAd.facebook_page_id")

class Prompt(Base):
    __tablename__ = "prompts"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    variables = Column(JSON, nullable=True)  # List of variable names
    template = Column(Text, nullable=False)  # The actual prompt template
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class AdStyle(Base):
    __tablename__ = "ad_styles"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    best_for = Column(JSON, nullable=True)  # List of industries
    visual_layout = Column(String, nullable=True)
    psychology = Column(Text, nullable=True)
    mood = Column(String, nullable=True)
    lighting = Column(String, nullable=True)
    composition = Column(String, nullable=True)
    design_style = Column(String, nullable=True)
    prompt = Column(Text, nullable=True)  # Image generation prompt
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class BrandScrape(Base):
    """Tracks scraping sessions for a specific Facebook page/brand."""
    __tablename__ = "brand_scrapes"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_brand_scrapes_id_org'),
        Index('ix_brand_scrapes_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    brand_name = Column(String, nullable=False, index=True)  # User-defined name, also R2 folder name
    page_id = Column(String, nullable=False)  # FB page ID from URL
    page_name = Column(String, nullable=True)  # Actual FB page name (discovered during scrape)
    page_url = Column(String, nullable=False)  # Original FB Ads Library URL
    total_ads = Column(Integer, default=0)  # Total ads found
    media_downloaded = Column(Integer, default=0)  # Successfully downloaded media count
    status = Column(String, default='pending')  # pending, scraping, completed, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    ads = relationship("BrandScrapedAd", back_populates="brand_scrape", cascade="all, delete-orphan",
                       primaryjoin="BrandScrape.id == BrandScrapedAd.brand_scrape_id", foreign_keys="BrandScrapedAd.brand_scrape_id")


class BrandScrapedAd(Base):
    """Individual ad scraped from a brand's Facebook page with media stored on R2."""
    __tablename__ = "brand_scraped_ads"
    __table_args__ = (
        ForeignKeyConstraint(['brand_scrape_id', 'organization_id'], ['brand_scrapes.id', 'brand_scrapes.organization_id'],
                             name='fk_brand_scraped_ads_scrape_org', ondelete='CASCADE'),
        Index('ix_brand_scraped_ads_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = LEGACY row in quarantine: invisible to every organization).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    brand_scrape_id = Column(String, ForeignKey('brand_scrapes.id', ondelete='CASCADE'), nullable=False)
    external_id = Column(String, nullable=False, index=True)  # FB ad library ID
    page_name = Column(String, nullable=True)  # Facebook page name
    page_link = Column(String, nullable=True)  # Link to page's ads in library
    headline = Column(String, nullable=True)
    ad_copy = Column(Text, nullable=True)
    cta_text = Column(String, nullable=True)
    media_type = Column(String, nullable=True)  # image, video, carousel
    media_urls = Column(JSON, nullable=True)  # R2 URLs for downloaded media
    original_media_urls = Column(JSON, nullable=True)  # Original FB media URLs
    platforms = Column(JSON, nullable=True)  # ['facebook', 'instagram']
    start_date = Column(String, nullable=True)
    ad_link = Column(String, nullable=True)  # FB Ads Library link
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    brand_scrape = relationship("BrandScrape", back_populates="ads",
                                primaryjoin="BrandScrape.id == BrandScrapedAd.brand_scrape_id", foreign_keys="BrandScrapedAd.brand_scrape_id")


class ApiKey(Base):
    """Machine-to-machine key (e.g. the Hermes Telegram bot). Hashed at rest —
    the plaintext key is shown once at creation time and never stored or logged."""
    __tablename__ = "api_keys"
    __table_args__ = (Index('ix_api_keys_organization_id', 'organization_id'),)

    id = Column(String, primary_key=True, default=generate_uuid)
    # Owner organization. NULL = LEGACY key: it authenticates nobody (quarantine)
    # until an operator binds it explicitly.
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    name = Column(String, nullable=False)
    key_hash = Column(String, unique=True, nullable=False, index=True)
    # Allowed values: "ads:read", "ads:draft". "ads:publish" / "ads:spend" do not
    # exist as scopes for bot keys — enforced in app.core.deps, not just policy text.
    scopes = Column(JSON, nullable=False, default=list)
    created_by_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    created_by = relationship("User")


class GoogleAdsConnection(Base):
    """One connected Google Ads account, tokens encrypted at rest via Fernet
    (see app.core.token_encryption)."""
    __tablename__ = "google_ads_connections"
    __table_args__ = (
        UniqueConstraint('organization_id', 'customer_id', name='uq_google_ads_connections_org_customer'),
        Index('ix_google_ads_connections_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    # Tenant owner (NULL = legacy per-user connection in quarantine).
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    # Manager (MCC) account to log in through, chosen per organization. There is
    # deliberately NO server-wide fallback (it would be shared by every tenant).
    login_customer_id = Column(String, nullable=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(String, nullable=False)  # Google Ads customer ID (no dashes)
    account_name = Column(String, nullable=True)
    encrypted_refresh_token = Column(Text, nullable=False)
    encrypted_access_token = Column(Text, nullable=True)
    access_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User")


class MetaAdsConnection(Base):
    """One Meta Ads account authorized by a user OAuth grant."""
    __tablename__ = "meta_ads_connections"
    __table_args__ = (
        UniqueConstraint('organization_id', 'ad_account_id', name='uq_meta_ads_connections_org_account'),
        Index('ix_meta_ads_connections_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ad_account_id = Column(String, nullable=False)
    account_name = Column(String, nullable=True)
    encrypted_access_token = Column(Text, nullable=False)
    access_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User")


class TikTokAdsConnection(Base):
    """One TikTok Marketing API advertiser connection, with OAuth tokens
    encrypted at rest through app.core.token_encryption."""
    __tablename__ = "tiktok_ads_connections"
    __table_args__ = (
        UniqueConstraint('organization_id', 'advertiser_id', name='uq_tiktok_ads_connections_org_advertiser'),
        Index('ix_tiktok_ads_connections_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    advertiser_id = Column(String, nullable=False)
    account_name = Column(String, nullable=True)
    encrypted_refresh_token = Column(Text, nullable=False)
    encrypted_access_token = Column(Text, nullable=True)
    access_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User")


class MutationAudit(Base):
    """Immutable audit trail for every optimization mutation decision —
    auto-mode verdicts, manual approvals, previews, and rejections. Written
    by the auto-mode scheduler and the optimization endpoints; never updated."""
    __tablename__ = "mutation_audits"
    __table_args__ = (Index('ix_mutation_audits_organization_id', 'organization_id'),)

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    api_key_id = Column(String, nullable=True)         # machine actor, when the request came from a bot key
    approval_id = Column(String, nullable=True)        # spend approval this decision relates to
    provider = Column(String, nullable=False)          # meta / google / tiktok
    account_id = Column(String, nullable=False)
    action = Column(String, nullable=False)            # campaign.pause / budget.update / ...
    campaign_id = Column(String, nullable=True)
    payload_hash = Column(String, nullable=False)      # canonical sha256
    mode = Column(String, nullable=False)              # auto / manual / preview / rejected
    verdict_reason = Column(String, nullable=True)     # policy reason or 'policy-pass'
    executed = Column(Boolean, default=False)          # did a real mutation happen
    metrics = Column(JSON, nullable=True)              # snapshot: spend/cpa/ctr at decision time
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")


class AuditLog(Base):
    """Append-only security audit trail (member changes, denied access).

    ``organization_id`` is the organization the event is about; deliberately NOT
    a foreign key so the trail outlives users/organizations. The runtime role
    can only INSERT and SELECT (inside its own organization): no UPDATE/DELETE.
    Never store secrets, tokens, passwords or request bodies here.
    """
    __tablename__ = "audit_logs"
    # INSERT ... RETURNING would need SELECT visibility (denied rows are not
    # visible to the actor's organization), so never fetch server defaults back.
    __mapper_args__ = {"eager_defaults": False}
    __table_args__ = (
        Index('ix_audit_logs_organization_id', 'organization_id'),
        Index('ix_audit_logs_created_at', 'created_at'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, nullable=True)
    actor_user_id = Column(String, nullable=True)
    action = Column(String, nullable=False)       # e.g. member.role_changed / access.denied
    outcome = Column(String, nullable=False)      # success / denied
    target = Column(String, nullable=True)        # e.g. user id, permission name, METHOD path
    detail = Column(JSON, nullable=True)          # small, non-secret context
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AdAccountLink(Base):
    """Explicit authorization of ONE ad account for ONE organization.

    OAuth only produces *candidates* (status ``pending``); an organization member
    with ``connections:manage`` activates exactly the accounts the organization
    may operate. Every read/write against an ad platform requires an ``active``
    link of the caller's organization: a user who belongs to two organizations
    never shares accounts between them implicitly.
    """
    __tablename__ = "ad_account_links"
    __table_args__ = (
        UniqueConstraint('organization_id', 'provider', 'external_account_id', name='uq_ad_account_links_org_provider_account'),
        CheckConstraint("provider IN ('google_ads','meta','tiktok')", name='ck_ad_account_links_provider'),
        CheckConstraint("status IN ('pending','active','revoked')", name='ck_ad_account_links_status'),
        Index('ix_ad_account_links_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    provider = Column(String, nullable=False)
    external_account_id = Column(String, nullable=False)      # customer_id / ad_account_id / advertiser_id
    account_name = Column(String, nullable=True)
    login_customer_id = Column(String, nullable=True)         # Google MCC for this organization
    status = Column(String, nullable=False, default="pending", server_default="pending")
    authorized_by_user_id = Column(String, nullable=True)
    authorized_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SpendApproval(Base):
    """Persisted human approval for an operation that can spend money.

    Lifecycle: ``pending`` -> ``approved`` (a human with ``ads:spend``; never an API
    key) -> ``consumed`` (exactly once, atomically, by the executing endpoint).
    The approval is bound to organization, account, action and payload hash and
    carries the budget scope the approver accepted. ``idempotency_key`` is unique
    per organization: repeating a request returns the stored result instead of
    executing twice.
    """
    __tablename__ = "spend_approvals"
    __table_args__ = (
        UniqueConstraint('organization_id', 'idempotency_key', name='uq_spend_approvals_org_idem'),
        CheckConstraint("status IN ('pending','approved','rejected','consumed','expired')", name='ck_spend_approvals_status'),
        Index('ix_spend_approvals_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True)
    provider = Column(String, nullable=False)
    account_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    payload_hash = Column(String, nullable=False)
    requested_budget_micros = Column(BigInteger, nullable=True)   # what the payload asks for
    max_budget_micros = Column(BigInteger, nullable=True)         # scope granted by the approver
    idempotency_key = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", server_default="pending")
    requested_by_user_id = Column(String, nullable=True)
    requested_by_api_key_id = Column(String, nullable=True)
    approved_by_user_id = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OAuthState(Base):
    """Server-side record of one OAuth authorization attempt (single use).

    Control table (no RLS): the unauthenticated provider callback must be able to
    find its state before any organization context exists. It stores only a hash
    of the browser-binding cookie, never a token.
    """
    __tablename__ = "oauth_states"
    __table_args__ = (Index('ix_oauth_states_expires_at', 'expires_at'),)

    id = Column(String, primary_key=True, default=generate_uuid)   # the state's jti
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False)
    cookie_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# AI Gateway (etapa 02.1): per-organization credentials (BYOK), model catalog,
# routing, budgets and usage. All tenant tables (RLS); no prompts or secrets are
# ever stored in the usage log.
# ---------------------------------------------------------------------------
class AiCredential(Base):
    """An organization's own API key for one AI provider. The key is Fernet-encrypted
    at rest, shown only as its last four characters, rotated in place and WIPED on
    revocation. At most one ACTIVE credential per (organization, provider)."""
    __tablename__ = "ai_credentials"
    __table_args__ = (
        CheckConstraint("status IN ('active','revoked')", name='ck_ai_credentials_status'),
        CheckConstraint("char_length(last4) <= 4", name='ck_ai_credentials_last4'),
        Index('ix_ai_credentials_organization_id', 'organization_id'),
        Index('uq_ai_credentials_active_provider', 'organization_id', 'provider', unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    provider = Column(String, nullable=False)
    label = Column(String, nullable=True)
    encrypted_key = Column(Text, nullable=False, default="")
    last4 = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="active", server_default="active")
    created_by_user_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_verification_ok = Column(Boolean, nullable=True)


class AiModel(Base):
    """A model an organization enabled. Names, capabilities and prices are DATA
    (entered or discovered), never constants in the code."""
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint('organization_id', 'provider', 'model_id', name='uq_ai_models_org_provider_model'),
        UniqueConstraint('id', 'organization_id', name='uq_ai_models_id_org'),
        CheckConstraint("input_price_micros_per_mtok IS NULL OR input_price_micros_per_mtok >= 0", name='ck_ai_models_in_price'),
        CheckConstraint("output_price_micros_per_mtok IS NULL OR output_price_micros_per_mtok >= 0", name='ck_ai_models_out_price'),
        CheckConstraint("cached_input_price_micros_per_mtok IS NULL OR cached_input_price_micros_per_mtok >= 0", name='ck_ai_models_cached_price'),
        Index('ix_ai_models_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    provider = Column(String, nullable=False)
    model_id = Column(String, nullable=False)            # the provider's own identifier
    display_name = Column(String, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    supports_tools = Column(Boolean, nullable=False, default=False, server_default="false")
    supports_vision = Column(Boolean, nullable=False, default=False, server_default="false")
    supports_json = Column(Boolean, nullable=False, default=False, server_default="false")
    context_window = Column(Integer, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    # USD (in millionths) per ONE MILLION tokens; NULL = unknown price.
    input_price_micros_per_mtok = Column(BigInteger, nullable=True)
    output_price_micros_per_mtok = Column(BigInteger, nullable=True)
    # Optional price of prompt tokens served from a provider cache; NULL = not informed
    # (cached tokens are then charged at the input price: never under-counted).
    cached_input_price_micros_per_mtok = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiRouting(Base):
    """Which model serves an operation: a primary and ordered fallbacks."""
    __tablename__ = "ai_routing"
    __table_args__ = (
        UniqueConstraint('organization_id', 'operation', name='uq_ai_routing_org_operation'),
        ForeignKeyConstraint(['primary_model_id', 'organization_id'], ['ai_models.id', 'ai_models.organization_id'],
                             name='fk_ai_routing_primary_org', ondelete='SET NULL (primary_model_id)'),
        Index('ix_ai_routing_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    operation = Column(String, nullable=False)           # 'default' or a concrete operation name
    primary_model_id = Column(String, nullable=True)
    fallback_model_ids = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiBudget(Base):
    """Spending limit of one organization for one period, with the counters that make
    reservation atomic (limit check + reservation = ONE UPDATE)."""
    __tablename__ = "ai_budgets"
    __table_args__ = (
        UniqueConstraint('organization_id', 'period', name='uq_ai_budgets_org_period'),
        CheckConstraint("period IN ('daily','monthly')", name='ck_ai_budgets_period'),
        CheckConstraint("limit_micros >= 0 AND spent_micros >= 0 AND reserved_micros >= 0", name='ck_ai_budgets_nonneg'),
        Index('ix_ai_budgets_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    period = Column(String, nullable=False)
    limit_micros = Column(BigInteger, nullable=False)    # USD in millionths
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    window_start = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    spent_micros = Column(BigInteger, nullable=False, default=0, server_default="0")
    reserved_micros = Column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiUsageLog(Base):
    """One provider attempt. Append-only for the application role; never holds prompts,
    completions, keys or provider error bodies."""
    __tablename__ = "ai_usage_logs"
    __table_args__ = (
        CheckConstraint("status IN ('success','error')", name='ck_ai_usage_status'),
        Index('ix_ai_usage_logs_org_created', 'organization_id', 'created_at'),
        Index('ix_ai_usage_logs_org_user', 'organization_id', 'user_id'),
    )
    __mapper_args__ = {"eager_defaults": False}

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    user_id = Column(String, nullable=True)
    request_id = Column(String, nullable=False)          # groups the attempts of one gateway call
    attempt = Column(Integer, nullable=False, default=1)
    operation = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    model_id = Column(String, nullable=False)
    credential_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    error_kind = Column(String, nullable=True)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    cached_input_tokens = Column(BigInteger, nullable=False, default=0)
    reasoning_tokens = Column(BigInteger, nullable=False, default=0)
    estimated_cost_micros = Column(BigInteger, nullable=True)   # what was reserved
    cost_micros = Column(BigInteger, nullable=True)             # computed from the provider-reported usage
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AiBudgetReservation(Base):
    """One ACTIVE reservation against an ``ai_budgets`` window. Deleted when settled/released;
    one that outlives the TTL belongs to a process that died and is converted to spent
    (see app.ai.budget.recover_abandoned)."""
    __tablename__ = "ai_budget_reservations"
    __table_args__ = (
        CheckConstraint("period IN ('daily','monthly')", name='ck_ai_reservations_period'),
        CheckConstraint("amount_micros >= 0", name='ck_ai_reservations_amount'),
        Index('ix_ai_budget_reservations_org_created', 'organization_id', 'created_at'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    period = Column(String, nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    amount_micros = Column(BigInteger, nullable=False)
    request_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ----------------------------------------------------------------------------- AI agents (etapa 02.3)
class AgentTask(Base):
    """One request for the agent system: a supervised multi-agent plan, or one specialist (``agent_key``).

    ``lease_owner`` / ``lease_expires_at`` are a FENCING lease: only the executor holding the lease may
    change the task; a task whose lease expired belongs to a dead process and is re-queued (or failed
    after ``max_attempts``). See app.agents.executor."""
    __tablename__ = "agent_tasks"
    __table_args__ = (
        UniqueConstraint('id', 'organization_id', name='uq_agent_tasks_id_org'),
        CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name='ck_agent_tasks_status'),
        Index('ix_agent_tasks_org_status', 'organization_id', 'status'),
        Index('ix_agent_tasks_org_created', 'organization_id', 'created_at'),
        Index('uq_agent_tasks_idempotency', 'organization_id', 'idempotency_key', unique=True,
              postgresql_where=text("idempotency_key IS NOT NULL")),
        Index('ix_agent_tasks_running_lease', 'lease_expires_at', postgresql_where=text("status = 'running'")),
        Index('ix_agent_tasks_claimable', 'available_at', 'created_at', postgresql_where=text("status = 'pending'")),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    created_by_user_id = Column(String, nullable=True)
    agent_key = Column(String, nullable=True)           # NULL = supervised plan
    objective = Column(Text, nullable=False)
    input_context = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="pending")
    plan = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_kind = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    lease_owner = Column(String, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    available_at = Column(DateTime(timezone=True), nullable=True)   # a pending task is not claimable before this (retry back-off)
    worker_id = Column(String, nullable=True)                       # worker process holding / last holding the task (diagnostics)
    limits = Column(JSON, nullable=True)
    max_cost_micros = Column(BigInteger, nullable=True)
    total_cost_micros = Column(BigInteger, nullable=False, default=0)
    total_input_tokens = Column(BigInteger, nullable=False, default=0)
    total_output_tokens = Column(BigInteger, nullable=False, default=0)
    step_count = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String, nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    """One specialist execution inside a task (the supervisor's delegation unit)."""
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(['task_id', 'organization_id'], ['agent_tasks.id', 'agent_tasks.organization_id'],
                             name='fk_agent_runs_task_org', ondelete='CASCADE'),
        UniqueConstraint('id', 'organization_id', name='uq_agent_runs_id_org'),
        UniqueConstraint('task_id', 'sequence', name='uq_agent_runs_task_sequence'),
        CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name='ck_agent_runs_status'),
        Index('ix_agent_runs_org_task', 'organization_id', 'task_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    task_id = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False)
    plan_step_id = Column(String, nullable=True)
    agent_key = Column(String, nullable=False)
    agent_version = Column(String, nullable=False)
    instructions_hash = Column(String, nullable=False)
    objective = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")
    output = Column(JSON, nullable=True)
    error_kind = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    step_count = Column(Integer, nullable=False, default=0)
    total_cost_micros = Column(BigInteger, nullable=False, default=0)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AgentStep(Base):
    """Append-only history of what happened (model turn, tool call, plan note...). Inputs and outputs are
    redacted and truncated BEFORE they are stored; prompts and raw provider bodies are never kept."""
    __tablename__ = "agent_steps"
    __table_args__ = (
        ForeignKeyConstraint(['task_id', 'organization_id'], ['agent_tasks.id', 'agent_tasks.organization_id'],
                             name='fk_agent_steps_task_org', ondelete='CASCADE'),
        UniqueConstraint('task_id', 'sequence', name='uq_agent_steps_task_sequence'),
        CheckConstraint("status IN ('ok','error')", name='ck_agent_steps_status'),
        Index('ix_agent_steps_org_task', 'organization_id', 'task_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    task_id = Column(String, nullable=False)
    run_id = Column(String, nullable=True)
    sequence = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)             # plan | llm | tool | consolidate | note | system
    name = Column(String, nullable=True)              # tool name / operation
    status = Column(String, nullable=False, default="ok")
    error_kind = Column(String, nullable=True)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    injection_suspected = Column(Boolean, nullable=False, default=False)
    gateway_request_id = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    model_id = Column(String, nullable=True)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    cost_micros = Column(BigInteger, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AgentSetting(Base):
    """Per-organization switch and (capped) limit overrides of one agent."""
    __tablename__ = "agent_settings"
    __table_args__ = (
        UniqueConstraint('organization_id', 'agent_key', name='uq_agent_settings_org_agent'),
        CheckConstraint("max_steps IS NULL OR max_steps > 0", name='ck_agent_settings_steps'),
        CheckConstraint("timeout_seconds IS NULL OR timeout_seconds > 0", name='ck_agent_settings_timeout'),
        Index('ix_agent_settings_organization_id', 'organization_id'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    agent_key = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    max_steps = Column(Integer, nullable=True)
    timeout_seconds = Column(Integer, nullable=True)
    updated_by_user_id = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentWorker(Base):
    """Liveness of one agent worker process (etapa 02.6). NOT tenant data: no organization, no task content."""
    __tablename__ = "agent_workers"
    __table_args__ = (
        CheckConstraint("status IN ('running','stopped')", name='ck_agent_workers_status'),
        Index('ix_agent_workers_last_seen', 'last_seen_at'),
    )

    worker_id = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    active_tasks = Column(Integer, nullable=False, default=0)
    concurrency = Column(Integer, nullable=False, default=1)
