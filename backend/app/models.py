from sqlalchemy import (
    Column, String, Integer, ForeignKey, DateTime, Text, JSON, Table, Boolean,
    UniqueConstraint, ForeignKeyConstraint, CheckConstraint, Index,
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

    id = Column(String, primary_key=True, default=generate_uuid)
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

    id = Column(String, primary_key=True, default=generate_uuid)
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

    id = Column(String, primary_key=True, default=generate_uuid)
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

    id = Column(String, primary_key=True, default=generate_uuid)
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

    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
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
