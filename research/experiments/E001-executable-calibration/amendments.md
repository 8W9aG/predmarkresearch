# E001 amendments

The preregistration is left unchanged so that its recorded hash stays valid. Changes made after
registration are listed here.

1. **2026-11-06, before any outcome data was analyzed.** The first collection attempt failed on every
   archive request, because the archive rejects `series_ticker` combined with `mve_filter`. It was
   stopped and its output deleted. Combo markets are now removed client-side using
   `mve_collection_ticker`. The population is unchanged.
2. **2026-11-06, before any outcome data was analyzed.** A schema check showed archived candles name
   the closing quote `close`, while live candles use `close_dollars`. The parser now reads both. While
   checking this, one archived candle for a single market (KXHIGHNY-26SEP05-T86) was printed,
   including its quotes but not its result.
