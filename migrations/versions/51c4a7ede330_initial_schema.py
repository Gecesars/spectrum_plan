"""initial schema

Revision ID: 51c4a7ede330
Revises: 
Create Date: 2025-12-01 09:57:17.660854

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '51c4a7ede330'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Schema já criada via init_db; esta revisão atua apenas como marker inicial.
    pass


def downgrade():
    pass
