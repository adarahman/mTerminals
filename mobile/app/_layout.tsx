import { Stack } from 'expo-router';
import { MarketContextProvider } from '../src/state/market-context';

export default function RootLayout() {
  return (
    <MarketContextProvider>
      <Stack screenOptions={{ headerShown: false }} />
    </MarketContextProvider>
  );
}