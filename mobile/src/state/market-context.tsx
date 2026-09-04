import React, {
  createContext,
  useContext,
  useMemo,
  useState,
} from 'react';

type MarketContextValue = {
  symbol: string;
  setSymbol: (value: string) => void;

  expiry: string | null;
  setExpiry: (value: string | null) => void;

  broker: string;
  setBroker: (value: string) => void;
};

const MarketContext = createContext<MarketContextValue | null>(null);

export function MarketContextProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [symbol, setSymbol] = useState('NIFTY');
  const [expiry, setExpiry] = useState<string | null>(null);
  const [broker, setBroker] = useState('SMARTAPI');

  const value = useMemo(
    () => ({
      symbol,
      setSymbol,
      expiry,
      setExpiry,
      broker,
      setBroker,
    }),
    [symbol, expiry, broker],
  );

  return (
    <MarketContext.Provider value={value}>
      {children}
    </MarketContext.Provider>
  );
}

export function useMarketContext() {
  const context = useContext(MarketContext);

  if (!context) {
    throw new Error(
      'useMarketContext must be used inside MarketContextProvider',
    );
  }

  return context;
}