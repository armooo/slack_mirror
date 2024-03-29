"""init schema

Revision ID: 432447eda104
Revises: None
Create Date: 2015-05-17 12:26:24.997724

"""

# revision identifiers, used by Alembic.
revision = '432447eda104'
down_revision = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.create_table('site_user',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('email', sa.UnicodeText(), nullable=False),
    sa.Column('last_log', sa.DateTime(), nullable=False),
    sa.Column('access_token', sa.Text(), nullable=False),
    sa.Column('zulip_key', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('site_user')
    ### end Alembic commands ###
