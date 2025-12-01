from __future__ import annotations

from typing import Dict


class RegulatoryStandard:
    """
    Enforces Anatel protection ratios (PR) for FM and TV.
    """
    
    # FM Protection Ratios (Res. 67 / Act 112)
    # Offset (kHz) -> PR (dB)
    FM_PR_TABLE: Dict[int, float] = {
        0: 45.0,    # Co-channel
        200: 6.0,   # 1st Adj
        400: -20.0, # 2nd Adj
        600: -40.0  # 3rd Adj
    }

    # Digital TV Protection Ratios (Act 932)
    # Offset (Channels) -> PR (dB)
    TV_PR_TABLE: Dict[int, float] = {
        0: 23.0,   # Co-channel
        -1: -28.0, # Lower Adj
        1: -27.0   # Upper Adj
    }

    @classmethod
    def get_required_pr(cls, service_type: str, offset: float) -> float:
        """
        Returns the required Protection Ratio (dB).
        
        :param service_type: "FM" or "TV"
        :param offset: Frequency difference in kHz (FM) or Channel difference (TV)
        """
        service = service_type.upper()
        
        if service == "FM":
            # Normalize to absolute and nearest 200
            abs_offset = abs(offset)
            
            # Find closest key in the table
            closest_offset = min(cls.FM_PR_TABLE.keys(), key=lambda k: abs(k - abs_offset))
            
            # If the deviation from a standard offset is significant (e.g. > 100kHz),
            # it implies the frequency is not one of the regulated relationships 
            # (or is beyond 3rd adj).
            if abs(closest_offset - abs_offset) > 100:
                return -999.0 
                
            return cls.FM_PR_TABLE[closest_offset]

        elif service == "TV":
            # Offset is channel difference (int)
            channel_offset = int(offset)
            if channel_offset in cls.TV_PR_TABLE:
                return cls.TV_PR_TABLE[channel_offset]
            else:
                return -999.0 # Unregulated / No interference

        else:
            raise ValueError(f"Unknown service type: {service_type}")
