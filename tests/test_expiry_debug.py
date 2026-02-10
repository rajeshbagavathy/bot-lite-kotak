#!/usr/bin/env python3
"""
Debug script to test expiry fetch for both NIFTY and SENSEX
Run this before starting the bot to see what expiry data is available
"""

import os
import sys
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import INDEX_CONFIGS, load_credentials
from xts_client import XTSClient

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def test_expiry_fetch():
    """Test expiry fetch for both indices"""
    
    logger.info("=" * 80)
    logger.info("🧪 EXPIRY DEBUG TEST")
    logger.info("=" * 80)
    
    try:
        logger.info("\n📦 Loading credentials...")
        creds = load_credentials()
        logger.info(f"✓ Credentials loaded: API Key={creds['api_key'][:10]}..., Client ID={creds['client_id']}")
        
        logger.info("\n🔑 Creating XTS client...")
        client = XTSClient(
            api_key=creds["api_key"],
            api_secret=creds["api_secret"],
            market_api_key=creds["market_api_key"],
            market_api_secret=creds["market_api_secret"],
            source="WEBAPI",
            client_id=creds["client_id"],
        )
        
        logger.info("\n🔐 Logging in to XTS...")
        client.login()
        logger.info("✓ Login successful")
        
        logger.info("\n" + "=" * 80)
        logger.info("Testing expiry fetch for each index...")
        logger.info("=" * 80)
        
        for index_name, config in INDEX_CONFIGS.items():
            logger.info(f"\n📊 Testing {index_name}:")
            logger.info(f"  Option Exchange: {config.option_exchange_segment}")
            logger.info(f"  Option LTP Segment: {config.option_ltp_segment}")
            logger.info(f"  FNO Symbol: {config.fno_symbol}")
            
            expiries = client.get_expiry_dates(config)
            
            if expiries:
                logger.info(f"  ✓ Found {len(expiries)} expiries:")
                for exp in expiries[:5]:  # Show first 5
                    logger.info(f"    - {exp}")
            else:
                logger.warning(f"  ❌ NO EXPIRIES RETURNED for {index_name}")
        
        logger.info("\n" + "=" * 80)
        logger.info("✓ Test completed")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"❌ Error during test: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    test_expiry_fetch()
