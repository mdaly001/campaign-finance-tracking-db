"""Debug script to test loading a single FEC table."""
import zipfile
import io
import urllib.request
import hashlib
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from sqlalchemy import create_engine, text
from core.etl.tsv import TSVReader
from core.etl.loader import TableLoader, LoadConfig
from core.etl.checkpoint import LoadCheckpoint

def load_fec_cm():
    """Load FEC_CM (cm.txt) with detailed debugging."""
    engine = create_engine("postgresql://cfdb:cfdb@172.19.0.3:5432/cfdb", pool_size=1)
    
    # Download cm24.zip
    url = "https://www.fec.gov/files/bulk-downloads/2024/cm24.zip"
    logger.info("Downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=60)
    data = resp.read()
    logger.info("Downloaded %d bytes", len(data))
    
    # Extract cm.txt
    zf = zipfile.ZipFile(io.BytesIO(data))
    raw = zf.read("cm.txt")
    logger.info("Extracted cm.txt: %d bytes", len(raw))
    
    # Compute hash
    file_hash = hashlib.sha256(raw).hexdigest()
    logger.info("File hash: %s", file_hash[:12])
    
    # Add header row
    column_names = [
        "committee_id", "committee_name", "treasurer_name", "street_1", "street_2",
        "city", "state", "zip", "organization_type", "committee_type",
        "type_designation", "connected_organization_name", "affiliation",
        "fec_election_year",
    ]
    header = "|".join(column_names)
    data_with_header = header.encode("utf-8") + b"\n" + raw
    
    # Check if already loaded
    checkpoint = LoadCheckpoint(engine)
    if checkpoint.is_loaded("fec.fec_committees", file_hash, source="fec"):
        logger.info("Already loaded (checkpoint exists)")
        return
    
    # Build load config
    config = LoadConfig(
        table_name="fec.fec_committees",
        tsv_files=["cm24.zip"],
        conflict_columns=["committee_id"],
        type_coercions={},
        required_columns=["committee_id"],
        skip_columns=["__table__", "__file_hash__"],
        source="fec",
    )
    
    # Create loader with FEC reader
    fec_reader = TSVReader(has_header=True, delimiter="|", empty_to_none=True)
    loader = TableLoader(engine, batch_size=1000, reader=fec_reader)
    
    # Load
    logger.info("Loading FEC_CM...")
    summary = loader.load(config, data_with_header)
    logger.info(
        "Result: %d read, %d upserted, %d skipped, %d failed",
        summary.rows_read,
        summary.rows_upserted,
        summary.rows_skipped,
        summary.rows_failed,
    )
    
    # Check table
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM fec.fec_committees"))
        count = result.scalar()
        logger.info("Table row count: %d", count)
    
    engine.dispose()

if __name__ == "__main__":
    load_fec_cm()
