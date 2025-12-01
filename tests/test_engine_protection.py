from app.core.engine.protection import RegulatoryStandard

def test_fm_protection_ratios():
    # Co-channel
    assert RegulatoryStandard.get_required_pr("FM", 0) == 45.0
    assert RegulatoryStandard.get_required_pr("FM", 50) == 45.0 # Nearest to 0
    
    # 1st Adj (200kHz)
    assert RegulatoryStandard.get_required_pr("FM", 200) == 6.0
    assert RegulatoryStandard.get_required_pr("FM", -200) == 6.0
    
    # 2nd Adj (400kHz)
    assert RegulatoryStandard.get_required_pr("FM", 400) == -20.0
    
    # 3rd Adj (600kHz)
    assert RegulatoryStandard.get_required_pr("FM", 600) == -40.0
    
    # Unregulated (>600kHz)
    assert RegulatoryStandard.get_required_pr("FM", 800) == -999.0

def test_tv_protection_ratios():
    # Co-channel
    assert RegulatoryStandard.get_required_pr("TV", 0) == 23.0
    
    # Adj
    assert RegulatoryStandard.get_required_pr("TV", 1) == -27.0
    assert RegulatoryStandard.get_required_pr("TV", -1) == -28.0
    
    # Unregulated
    assert RegulatoryStandard.get_required_pr("TV", 2) == -999.0
