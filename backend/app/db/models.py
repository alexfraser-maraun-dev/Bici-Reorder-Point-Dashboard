from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, ForeignKey, JSON
from sqlalchemy.sql import func
from app.db.database import Base

class ManagedSKU(Base):
    __tablename__ = "managed_skus"
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, unique=True, index=True)
    item_id = Column(String, index=True) # Lightspeed itemID
    product = Column(String, nullable=True)
    brand = Column(String, nullable=True)
    vendor = Column(String, nullable=True)
    category = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    added_by = Column(String, nullable=True) # To track who uploaded
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class SKULocationPolicy(Base):
    __tablename__ = "sku_location_policy"
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, ForeignKey("managed_skus.sku"))
    location_id = Column(String, index=True) # Lightspeed shopID
    trailing_days = Column(Integer, default=30)
    forecast_days = Column(Integer, default=60)
    safety_days = Column(Integer, default=7)
    auto_update_enabled = Column(Boolean, default=False)
    locked = Column(Boolean, default=False)
    manual_reorder_point_override = Column(Integer, nullable=True)
    manual_desired_level_override = Column(Integer, nullable=True)
    notes = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    id = Column(Integer, primary_key=True, index=True)
    run_type = Column(String) # 'scheduled' or 'manual'
    triggered_by = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String) # 'running', 'completed', 'failed'
    row_count = Column(Integer, nullable=True)

class RecommendationRow(Base):
    __tablename__ = "recommendation_rows"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("recommendation_runs.id"))
    item_id = Column(String)
    sku = Column(String)
    location_id = Column(String)
    lead_time_days = Column(Integer)
    on_hand_units = Column(Integer)
    on_order_units = Column(Integer)
    trailing_units_sold = Column(Float)
    daily_sales = Column(Float)
    recommended_reorder_point = Column(Integer)
    recommended_desired_inventory = Column(Integer)
    suggested_buy_qty = Column(Integer)
    needs_order = Column(Boolean)
    current_reorder_point = Column(Integer)
    current_desired_inventory = Column(Integer)
    changed_flag = Column(Boolean, default=False) # True if recommended != current
    locked_flag = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class WritebackLog(Base):
    __tablename__ = "writeback_logs"
    id = Column(Integer, primary_key=True, index=True)
    recommendation_row_id = Column(Integer, ForeignKey("recommendation_rows.id"))
    sku = Column(String)
    location_id = Column(String)
    old_reorder_point = Column(Integer)
    new_reorder_point = Column(Integer)
    old_desired_inventory = Column(Integer)
    new_desired_inventory = Column(Integer)
    triggered_by = Column(String)
    status = Column(String) # 'success', 'failed'
    response_payload = Column(JSON, nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
class VelocitySnapshot(Base):
    __tablename__ = "velocity_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    system_id = Column(String, index=True)
    location = Column(String, index=True)
    daily_sales = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
