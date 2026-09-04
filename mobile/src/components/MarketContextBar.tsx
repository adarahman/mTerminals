import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useMarketContext } from '../state/market-context';

const SYMBOLS = ['NIFTY', 'BANKNIFTY', 'SENSEX'];
const BROKERS = ['SMARTAPI', 'UPSTOX', 'KOTAK', 'SHOONYA', 'BREEZE', 'KITE'];

const EXPIRIES = [
  '10-SEP-2026',
  '17-SEP-2026',
  '24-SEP-2026',
];

export function MarketContextBar() {
  const {
    symbol,
    setSymbol,
    expiry,
    setExpiry,
    broker,
    setBroker,
  } = useMarketContext();

  const nextSymbol = () => {
    const index = SYMBOLS.indexOf(symbol);
    setSymbol(SYMBOLS[(index + 1) % SYMBOLS.length]);
  };

  const nextExpiry = () => {
    const current = expiry ?? EXPIRIES[0];
    const index = EXPIRIES.indexOf(current);

    setExpiry(
      EXPIRIES[
        index === -1
          ? 0
          : (index + 1) % EXPIRIES.length
      ],
    );
  };

  const nextBroker = () => {
    const index = BROKERS.indexOf(broker);
    setBroker(BROKERS[(index + 1) % BROKERS.length]);
  };

  return (
    <View style={styles.container}>
      <Pressable style={styles.item} onPress={nextSymbol}>
        <Text style={styles.primary}>{symbol} ▾</Text>
      </Pressable>

      <Pressable style={styles.item} onPress={nextExpiry}>
        <Text style={styles.secondary}>
          {expiry ?? 'EXPIRY'} ▾
        </Text>
      </Pressable>

      <Pressable style={styles.item} onPress={nextBroker}>
        <Text style={styles.secondary}>
          {broker} ●
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderRadius: 12,
    marginBottom: 16,
  },

  item: {
    paddingVertical: 6,
    paddingHorizontal: 6,
  },

  primary: {
    fontSize: 16,
    fontWeight: '700',
  },

  secondary: {
    fontSize: 13,
    fontWeight: '600',
  },
});