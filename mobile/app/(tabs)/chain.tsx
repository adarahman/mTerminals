import { useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MarketContextBar } from '../../src/components/MarketContextBar';
import { useMarketData } from '../../src/hooks/useMarketData';

const RANGE_OPTIONS = [3, 5, 10, 15, 9999] as const;

const TABS = [
  'Chain',
  'OI',
  'Greeks',
  'Capital',
  'Velocity',
  'Smart',
] as const;

type TabName = (typeof TABS)[number];

export default function ChainScreen() {
  const { payload, connectionStatus, error } = useMarketData();

  const market: any = payload?.market ?? payload ?? {};

  const [range, setRange] = useState<number>(5);
  const [tab, setTab] = useState<TabName>('Chain');
  const [expandedStrike, setExpandedStrike] =
    useState<number | null>(null);

  const connectionLabel =
    connectionStatus === 'connected'
      ? '● CONNECTED'
      : connectionStatus === 'reconnecting'
        ? '● RECONNECTING…'
        : connectionStatus === 'connecting'
          ? '● CONNECTING…'
          : connectionStatus === 'error'
            ? '● CONNECTION ERROR'
            : '● OFFLINE';

  const connectionColor =
    connectionStatus === 'connected'
      ? '#63d297'
      : connectionStatus === 'reconnecting' ||
          connectionStatus === 'connecting'
        ? '#e8b45b'
        : '#ef7777';

  const chain = useMemo(() => {
    const greekRows =
      Array.isArray(market.greeks)
        ? market.greeks
        : [];

    const greekMap = new Map<number, any>(
      greekRows.map((row: any) => [
        Number(row.strike),
        row,
      ]),
    );

    return (
      Array.isArray(market.chain)
        ? market.chain
        : []
    )
      .map((row: any) => {
        const g =
          greekMap.get(Number(row.strike)) ?? {};

        return {
          ...row,

          ceDelta:
            row.ceDelta ??
            row.ce_delta ??
            g.cDelta ??
            g.ceDelta,

          peDelta:
            row.peDelta ??
            row.pe_delta ??
            g.pDelta ??
            g.peDelta,

          ceGamma:
            row.ceGamma ??
            row.ce_gamma ??
            g.cGamma ??
            g.ceGamma,

          peGamma:
            row.peGamma ??
            row.pe_gamma ??
            g.pGamma ??
            g.peGamma,

          ceTheta:
            row.ceTheta ??
            row.ce_theta ??
            g.cTheta ??
            g.ceTheta,

          peTheta:
            row.peTheta ??
            row.pe_theta ??
            g.pTheta ??
            g.peTheta,

          ceVega:
            row.ceVega ??
            row.ce_vega ??
            g.cVega ??
            g.ceVega,

          peVega:
            row.peVega ??
            row.pe_vega ??
            g.pVega ??
            g.peVega,

          ceIv:
            row.ceIv ??
            row.ce_iv ??
            g.cIV ??
            g.ceIV ??
            g.cIv,

          peIv:
            row.peIv ??
            row.pe_iv ??
            g.pIV ??
            g.peIV ??
            g.pIv,
        };
      })
      .sort(
        (a: any, b: any) =>
          Number(a.strike) - Number(b.strike),
      );
  }, [market.chain, market.greeks]);

  const atm = Number(market.atm);

  const visibleRows = useMemo(() => {
    if (!chain.length) return [];

    if (range === 9999) {
      return chain;
    }

    let atmIndex = chain.findIndex(
      (row: any) =>
        row.atm ||
        Number(row.strike) === atm,
    );

    if (atmIndex < 0 && Number.isFinite(atm)) {
      let distance = Number.POSITIVE_INFINITY;

      chain.forEach((row: any, index: number) => {
        const currentDistance =
          Math.abs(Number(row.strike) - atm);

        if (currentDistance < distance) {
          distance = currentDistance;
          atmIndex = index;
        }
      });
    }

    if (atmIndex < 0) {
      return chain;
    }

    return chain.slice(
      Math.max(0, atmIndex - range),
      Math.min(
        chain.length,
        atmIndex + range + 1,
      ),
    );
  }, [chain, atm, range]);

  return (
    <SafeAreaView
      style={{
        flex: 1,
        backgroundColor: '#0b0d10',
      }}
      edges={['top']}
    >
      <MarketContextBar />

      <ScrollView
        contentContainerStyle={{
          padding: 14,
          paddingBottom: 50,
          gap: 12,
        }}
      >
        <View
          style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            gap: 12,
          }}
        >
          <View style={{ flex: 1 }}>
            <Text
              style={{
                color: '#ffffff',
                fontSize: 25,
                fontWeight: '800',
              }}
            >
              Option Chain
            </Text>

            <Text
              style={{
                color: '#7f8a99',
                fontSize: 12,
                marginTop: 4,
              }}
            >
              {market.symbol || '—'}
              {'  •  '}
              {market.expiry || '—'}
              {'  •  ATM '}
              {market.atm ?? '—'}
            </Text>
          </View>

          <Text
            style={{
              color: connectionColor,
              fontSize: 11,
              fontWeight: '700',
            }}
          >
            {connectionLabel}
          </Text>
        </View>

        {error ? (
          <Card>
            <Text style={{ color: '#ef7777' }}>
              {error}
            </Text>
          </Card>
        ) : null}

        <View
          style={{
            flexDirection: 'row',
            gap: 8,
          }}
        >
          <SummaryChip
            label="SPOT"
            value={formatNumber(market.spot, 2)}
          />

          <SummaryChip
            label="CE WALL"
            value={formatNumber(
              market.ceWall ??
                market.ce_wall,
              0,
            )}
          />

          <SummaryChip
            label="PE WALL"
            value={formatNumber(
              market.peWall ??
                market.pe_wall,
              0,
            )}
          />
        </View>

        {/* Mobile-native analytics tabs */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{
            gap: 7,
            paddingVertical: 2,
          }}
        >
          {TABS.map(name => {
            const active = tab === name;

            return (
              <Pressable
                key={name}
                onPress={() => setTab(name)}
                style={{
                  paddingHorizontal: 15,
                  paddingVertical: 10,
                  borderRadius: 12,
                  backgroundColor:
                    active
                      ? '#303946'
                      : '#15191f',
                  borderWidth: 1,
                  borderColor:
                    active
                      ? '#667385'
                      : '#242a32',
                }}
              >
                <Text
                  style={{
                    color:
                      active
                        ? '#ffffff'
                        : '#8f99a7',
                    fontSize: 12,
                    fontWeight: '800',
                  }}
                >
                  {name}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>

        <Card>
          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent:
                'space-between',
            }}
          >
            <Text style={styles.sectionTitle}>
              ATM RANGE
            </Text>

            <Text
              style={{
                color: '#707b89',
                fontSize: 11,
              }}
            >
              {visibleRows.length} strikes
            </Text>
          </View>

          <View
            style={{
              flexDirection: 'row',
              gap: 7,
              marginTop: 10,
            }}
          >
            {RANGE_OPTIONS.map(value => {
              const active = range === value;

              return (
                <Pressable
                  key={value}
                  onPress={() =>
                    setRange(value)
                  }
                  style={{
                    flex: 1,
                    paddingVertical: 8,
                    borderRadius: 9,
                    alignItems: 'center',
                    backgroundColor:
                      active
                        ? '#303946'
                        : '#101419',
                  }}
                >
                  <Text
                    style={{
                      color:
                        active
                          ? '#ffffff'
                          : '#7f8a99',
                      fontWeight: '700',
                      fontSize: 11,
                    }}
                  >
                    {value === 9999
                      ? 'All'
                      : `±${value}`}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </Card>

        {!visibleRows.length ? (
          <Card>
            <Text
              style={{
                color: '#8f99a7',
                textAlign: 'center',
                paddingVertical: 20,
              }}
            >
              Waiting for option-chain data…
            </Text>
          </Card>
        ) : null}

        {tab === 'Chain' ? (
          <ChainTab
            rows={visibleRows}
            atm={atm}
            ceWall={Number(
              market.ceWall ??
                market.ce_wall,
            )}
            peWall={Number(
              market.peWall ??
                market.pe_wall,
            )}
            expandedStrike={expandedStrike}
            onToggle={(strike: number) =>
              setExpandedStrike(
                expandedStrike === strike
                  ? null
                  : strike,
              )
            }
          />
        ) : null}

        {tab === 'OI' ? (
          <OITab
            rows={visibleRows}
            atm={atm}
          />
        ) : null}

        {tab === 'Greeks' ? (
          <GreeksTab
            rows={visibleRows}
            atm={atm}
          />
        ) : null}

        {tab === 'Capital' ? (
          <CapitalTab
            rows={visibleRows}
            atm={atm}
          />
        ) : null}

        {tab === 'Velocity' ? (
          <VelocityTab
            rows={visibleRows}
            market={market}
            atm={atm}
          />
        ) : null}

        {tab === 'Smart' ? (
          <SmartTab
            rows={visibleRows}
            market={market}
            atm={atm}
          />
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function ChainTab({
  rows,
  atm,
  ceWall,
  peWall,
  expandedStrike,
  onToggle,
}: any) {
  return (
    <View style={{ gap: 7 }}>
      <ThreeColumnHeader
        left="CALL"
        center="STRIKE"
        right="PUT"
      />

      {rows.map((row: any) => {
        const strike = Number(row.strike);

        const isAtm =
          Boolean(row.atm) ||
          strike === atm;

        const isCeWall =
          Number.isFinite(ceWall) &&
          strike === ceWall;

        const isPeWall =
          Number.isFinite(peWall) &&
          strike === peWall;

        const expanded =
          expandedStrike === strike;

        return (
          <Pressable
            key={String(strike)}
            onPress={() => onToggle(strike)}
            style={{
              backgroundColor: isAtm
                ? '#202832'
                : '#15191f',
              borderRadius: 13,
              borderWidth: isAtm ? 1 : 0,
              borderColor: '#586675',
              overflow: 'hidden',
            }}
          >
            <View
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                paddingHorizontal: 10,
                paddingVertical: 11,
              }}
            >
              <View style={{ flex: 1 }}>
                <Text
                  style={{
                    color: '#ef8d8d',
                    fontSize: 16,
                    fontWeight: '800',
                  }}
                >
                  {formatNumber(
                    firstValue(
                      row.ceLTP,
                      row.ceLtp,
                      row.ce_ltp,
                      row.callLtp,
                      row.call_ltp,
                    ),
                    2,
                  )}
                </Text>

                <Text
                  style={styles.mutedTiny}
                >
                  OI{' '}
                  {formatCompact(
                    firstValue(
                      row.ceOI,
                      row.ceOi,
                      row.ce_oi,
                      row.callOi,
                    ),
                  )}
                  {'  '}
                  Δ{' '}
                  {formatSignedCompact(
                    firstValue(
                      row.ceChgOI,
                      row.ceOiChg,
                      row.ce_oi_chg,
                      row.ceChangeOi,
                      row.ce_change_oi,
                    ),
                  )}
                </Text>
              </View>

              <View
                style={{
                  width: 92,
                  alignItems: 'center',
                }}
              >
                <Text
                  style={{
                    color: '#ffffff',
                    fontSize: 16,
                    fontWeight: '900',
                  }}
                >
                  {formatNumber(strike, 0)}
                </Text>

                <View
                  style={{
                    flexDirection: 'row',
                    gap: 4,
                    marginTop: 3,
                  }}
                >
                  {isAtm ? (
                    <Badge text="ATM" />
                  ) : null}

                  {isCeWall ? (
                    <Badge text="CE W" />
                  ) : null}

                  {isPeWall ? (
                    <Badge text="PE W" />
                  ) : null}
                </View>
              </View>

              <View
                style={{
                  flex: 1,
                  alignItems: 'flex-end',
                }}
              >
                <Text
                  style={{
                    color: '#70d69c',
                    fontSize: 16,
                    fontWeight: '800',
                  }}
                >
                  {formatNumber(
                    firstValue(
                      row.peLTP,
                      row.peLtp,
                      row.pe_ltp,
                      row.putLtp,
                      row.put_ltp,
                    ),
                    2,
                  )}
                </Text>

                <Text
                  style={styles.mutedTiny}
                >
                  Δ{' '}
                  {formatSignedCompact(
                    firstValue(
                      row.peChgOI,
                      row.peOiChg,
                      row.pe_oi_chg,
                      row.peChangeOi,
                      row.pe_change_oi,
                    ),
                  )}
                  {'  '}
                  OI{' '}
                  {formatCompact(
                    firstValue(
                      row.peOI,
                      row.peOi,
                      row.pe_oi,
                      row.putOi,
                    ),
                  )}
                </Text>
              </View>
            </View>

            {expanded ? (
              <View
                style={{
                  borderTopWidth: 1,
                  borderTopColor: '#252b33',
                  padding: 10,
                  gap: 8,
                }}
              >
                <DetailGrid
                  items={[
                    [
                      'CE IV',
                      formatNumber(
                        row.ceIv,
                        2,
                      ),
                    ],
                    [
                      'PE IV',
                      formatNumber(
                        row.peIv,
                        2,
                      ),
                    ],
                    [
                      'CE Δ',
                      formatNumber(
                        row.ceDelta,
                        3,
                      ),
                    ],
                    [
                      'PE Δ',
                      formatNumber(
                        row.peDelta,
                        3,
                      ),
                    ],
                    [
                      'CE Vol',
                      formatCompact(
                        firstValue(
                          row.ceVolume,
                          row.ce_volume,
                        ),
                      ),
                    ],
                    [
                      'PE Vol',
                      formatCompact(
                        firstValue(
                          row.peVolume,
                          row.pe_volume,
                        ),
                      ),
                    ],
                  ]}
                />
              </View>
            ) : null}
          </Pressable>
        );
      })}
    </View>
  );
}

function OITab({
  rows,
  atm,
}: any) {
  return (
    <View style={{ gap: 7 }}>
      <ThreeColumnHeader
        left="CALL OI"
        center="STRIKE"
        right="PUT OI"
      />

      {rows.map((row: any) => (
        <MetricStrikeRow
          key={String(row.strike)}
          strike={Number(row.strike)}
          atm={atm}
          leftPrimary={formatCompact(
            firstValue(
              row.ceOI,
              row.ceOi,
              row.ce_oi,
            ),
          )}
          leftSecondary={`Δ ${formatSignedCompact(
            firstValue(
              row.ceChgOI,
              row.ceOiChg,
              row.ce_oi_chg,
            ),
          )}`}
          rightPrimary={formatCompact(
            firstValue(
              row.peOI,
              row.peOi,
              row.pe_oi,
            ),
          )}
          rightSecondary={`Δ ${formatSignedCompact(
            firstValue(
              row.peChgOI,
              row.peOiChg,
              row.pe_oi_chg,
            ),
          )}`}
        />
      ))}
    </View>
  );
}

function GreeksTab({
  rows,
  atm,
}: any) {
  return (
    <View style={{ gap: 7 }}>
      <Text style={styles.helper}>
        Δ primary · IV / Γ / Θ underneath
      </Text>

      {rows.map((row: any) => (
        <MetricStrikeRow
          key={String(row.strike)}
          strike={Number(row.strike)}
          atm={atm}
          leftPrimary={`Δ ${formatNumber(
            row.ceDelta,
            3,
          )}`}
          leftSecondary={`IV ${formatNumber(
            row.ceIv,
            1,
          )} · Γ ${formatNumber(
            row.ceGamma,
            4,
          )} · Θ ${formatNumber(
            row.ceTheta,
            1,
          )}`}
          rightPrimary={`Δ ${formatNumber(
            row.peDelta,
            3,
          )}`}
          rightSecondary={`IV ${formatNumber(
            row.peIv,
            1,
          )} · Γ ${formatNumber(
            row.peGamma,
            4,
          )} · Θ ${formatNumber(
            row.peTheta,
            1,
          )}`}
        />
      ))}
    </View>
  );
}

function CapitalTab({
  rows,
  atm,
}: any) {
  return (
    <View style={{ gap: 7 }}>
      <Text style={styles.helper}>
        Capital / premium metrics when supplied by
        the backend
      </Text>

      {rows.map((row: any) => (
        <MetricStrikeRow
          key={String(row.strike)}
          strike={Number(row.strike)}
          atm={atm}
          leftPrimary={formatMoneyCompact(
            firstValue(
              row.ceCapitalFlow,
              row.ce_capital_flow,
              row.ceCapital,
              row.ce_capital,
              row.ceNotionalExposure,
              row.ce_notional_exposure,
            ),
          )}
          leftSecondary={`Locked ${formatMoneyCompact(
            firstValue(
              row.cePremiumLocked,
              row.ce_premium_locked,
            ),
          )}`}
          rightPrimary={formatMoneyCompact(
            firstValue(
              row.peCapitalFlow,
              row.pe_capital_flow,
              row.peCapital,
              row.pe_capital,
              row.peNotionalExposure,
              row.pe_notional_exposure,
            ),
          )}
          rightSecondary={`Locked ${formatMoneyCompact(
            firstValue(
              row.pePremiumLocked,
              row.pe_premium_locked,
            ),
          )}`}
        />
      ))}
    </View>
  );
}

function VelocityTab({
  rows,
  market,
  atm,
}: any) {
  const velocity =
    market.oiVelocity ??
    market.oi_velocity;

  const velocityRows =
    Array.isArray(velocity)
      ? velocity
      : Array.isArray(velocity?.rows)
        ? velocity.rows
        : [];

  const byStrike = new Map<number, any>(
    velocityRows.map((row: any) => [
      Number(row.strike),
      row,
    ]),
  );

  return (
    <View style={{ gap: 7 }}>
      <Text style={styles.helper}>
        OI velocity · 5m / 15m / 30m
      </Text>

      {rows.map((row: any) => {
        const v =
          byStrike.get(
            Number(row.strike),
          ) ?? row;

        return (
          <MetricStrikeRow
            key={String(row.strike)}
            strike={Number(row.strike)}
            atm={atm}
            leftPrimary={`5m ${formatSignedCompact(
              firstValue(
                v.ce5m,
                v.ce_5m,
                v.ceVelocity5m,
                v.ce_velocity_5m,
              ),
            )}`}
            leftSecondary={`15m ${formatSignedCompact(
              firstValue(
                v.ce15m,
                v.ce_15m,
                v.ceVelocity15m,
                v.ce_velocity_15m,
              ),
            )} · 30m ${formatSignedCompact(
              firstValue(
                v.ce30m,
                v.ce_30m,
                v.ceVelocity30m,
                v.ce_velocity_30m,
              ),
            )}`}
            rightPrimary={`5m ${formatSignedCompact(
              firstValue(
                v.pe5m,
                v.pe_5m,
                v.peVelocity5m,
                v.pe_velocity_5m,
              ),
            )}`}
            rightSecondary={`15m ${formatSignedCompact(
              firstValue(
                v.pe15m,
                v.pe_15m,
                v.peVelocity15m,
                v.pe_velocity_15m,
              ),
            )} · 30m ${formatSignedCompact(
              firstValue(
                v.pe30m,
                v.pe_30m,
                v.peVelocity30m,
                v.pe_velocity_30m,
              ),
            )}`}
          />
        );
      })}
    </View>
  );
}

function SmartTab({
  rows,
  market,
  atm,
}: any) {
  return (
    <View style={{ gap: 7 }}>
      <Card>
        <Text style={styles.sectionTitle}>
          SMART MONEY CONTEXT
        </Text>

        <DetailGrid
          items={[
            [
              'Bias',
              String(
                firstValue(
                  market.smartMoney?.bias,
                  market.smart_money?.bias,
                  market.engineBias,
                  market.engine_bias,
                ) ?? '—',
              ),
            ],
            [
              'PCR',
              formatNumber(
                firstValue(
                  market.totalPcr,
                  market.totalPCR,
                  market.pcr,
                ),
                2,
              ),
            ],
          ]}
        />
      </Card>

      {rows.map((row: any) => (
        <MetricStrikeRow
          key={String(row.strike)}
          strike={Number(row.strike)}
          atm={atm}
          leftPrimary={smartLabel(
            firstValue(
              row.ceBuildup,
              row.ce_buildup,
              row.ceSignal,
              row.ce_signal,
            ),
          )}
          leftSecondary={`Vol ${formatCompact(
            firstValue(
              row.ceVolume,
              row.ce_volume,
            ),
          )} · ΔOI ${formatSignedCompact(
            firstValue(
              row.ceChgOI,
              row.ceOiChg,
              row.ce_oi_chg,
            ),
          )}`}
          rightPrimary={smartLabel(
            firstValue(
              row.peBuildup,
              row.pe_buildup,
              row.peSignal,
              row.pe_signal,
            ),
          )}
          rightSecondary={`Vol ${formatCompact(
            firstValue(
              row.peVolume,
              row.pe_volume,
            ),
          )} · ΔOI ${formatSignedCompact(
            firstValue(
              row.peChgOI,
              row.peOiChg,
              row.pe_oi_chg,
            ),
          )}`}
        />
      ))}
    </View>
  );
}

function MetricStrikeRow({
  strike,
  atm,
  leftPrimary,
  leftSecondary,
  rightPrimary,
  rightSecondary,
}: any) {
  const isAtm = strike === atm;

  return (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor:
          isAtm
            ? '#202832'
            : '#15191f',
        borderRadius: 12,
        paddingHorizontal: 10,
        paddingVertical: 11,
        borderWidth: isAtm ? 1 : 0,
        borderColor: '#586675',
      }}
    >
      <View style={{ flex: 1 }}>
        <Text
          style={{
            color: '#ef8d8d',
            fontSize: 14,
            fontWeight: '800',
          }}
        >
          {leftPrimary || '—'}
        </Text>

        <Text style={styles.metricSecondary}>
          {leftSecondary || '—'}
        </Text>
      </View>

      <View
        style={{
          width: 86,
          alignItems: 'center',
        }}
      >
        <Text
          style={{
            color: '#ffffff',
            fontWeight: '900',
            fontSize: 15,
          }}
        >
          {formatNumber(strike, 0)}
        </Text>

        {isAtm ? (
          <Badge text="ATM" />
        ) : null}
      </View>

      <View
        style={{
          flex: 1,
          alignItems: 'flex-end',
        }}
      >
        <Text
          style={{
            color: '#70d69c',
            fontSize: 14,
            fontWeight: '800',
            textAlign: 'right',
          }}
        >
          {rightPrimary || '—'}
        </Text>

        <Text
          style={[
            styles.metricSecondary,
            { textAlign: 'right' },
          ]}
        >
          {rightSecondary || '—'}
        </Text>
      </View>
    </View>
  );
}

function ThreeColumnHeader({
  left,
  center,
  right,
}: {
  left: string;
  center: string;
  right: string;
}) {
  return (
    <View
      style={{
        flexDirection: 'row',
        paddingHorizontal: 10,
        paddingVertical: 6,
      }}
    >
      <Text
        style={[
          styles.columnHeader,
          {
            flex: 1,
            color: '#d78181',
          },
        ]}
      >
        {left}
      </Text>

      <Text
        style={[
          styles.columnHeader,
          {
            width: 92,
            textAlign: 'center',
          },
        ]}
      >
        {center}
      </Text>

      <Text
        style={[
          styles.columnHeader,
          {
            flex: 1,
            color: '#70c994',
            textAlign: 'right',
          },
        ]}
      >
        {right}
      </Text>
    </View>
  );
}

function Card({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <View
      style={{
        backgroundColor: '#15191f',
        borderRadius: 14,
        padding: 12,
      }}
    >
      {children}
    </View>
  );
}

function SummaryChip({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <View
      style={{
        flex: 1,
        backgroundColor: '#15191f',
        borderRadius: 12,
        paddingVertical: 10,
        paddingHorizontal: 8,
        alignItems: 'center',
      }}
    >
      <Text
        style={{
          color: '#6f7a88',
          fontSize: 9,
          fontWeight: '800',
        }}
      >
        {label}
      </Text>

      <Text
        style={{
          color: '#ffffff',
          fontSize: 14,
          fontWeight: '800',
          marginTop: 4,
        }}
      >
        {value}
      </Text>
    </View>
  );
}

function Badge({
  text,
}: {
  text: string;
}) {
  return (
    <View
      style={{
        backgroundColor: '#394451',
        borderRadius: 5,
        paddingHorizontal: 5,
        paddingVertical: 2,
        marginTop: 3,
      }}
    >
      <Text
        style={{
          color: '#dbe4ee',
          fontSize: 8,
          fontWeight: '900',
        }}
      >
        {text}
      </Text>
    </View>
  );
}

function DetailGrid({
  items,
}: {
  items: [string, string][];
}) {
  return (
    <View
      style={{
        flexDirection: 'row',
        flexWrap: 'wrap',
        gap: 8,
      }}
    >
      {items.map(([label, value]) => (
        <View
          key={label}
          style={{
            width: '47%',
            backgroundColor: '#101419',
            borderRadius: 9,
            padding: 9,
          }}
        >
          <Text
            style={{
              color: '#6f7a88',
              fontSize: 9,
              fontWeight: '800',
            }}
          >
            {label}
          </Text>

          <Text
            style={{
              color: '#ffffff',
              fontSize: 13,
              fontWeight: '700',
              marginTop: 3,
            }}
          >
            {value}
          </Text>
        </View>
      ))}
    </View>
  );
}

function firstValue(
  ...values: any[]
) {
  for (const value of values) {
    if (
      value !== undefined &&
      value !== null &&
      value !== ''
    ) {
      return value;
    }
  }

  return undefined;
}

function formatNumber(
  value: any,
  decimals = 2,
) {
  const n = Number(value);

  if (!Number.isFinite(n)) {
    return '—';
  }

  return n.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function formatCompact(value: any) {
  const n = Number(value);

  if (!Number.isFinite(n)) {
    return '—';
  }

  const abs = Math.abs(n);

  if (abs >= 1e7) {
    return `${(n / 1e7).toFixed(2)}Cr`;
  }

  if (abs >= 1e5) {
    return `${(n / 1e5).toFixed(2)}L`;
  }

  if (abs >= 1e3) {
    return `${(n / 1e3).toFixed(1)}K`;
  }

  return n.toLocaleString('en-IN');
}

function formatSignedCompact(value: any) {
  const n = Number(value);

  if (!Number.isFinite(n)) {
    return '—';
  }

  const result =
    formatCompact(Math.abs(n));

  if (n > 0) return `+${result}`;
  if (n < 0) return `-${result}`;

  return '0';
}

function formatMoneyCompact(value: any) {
  const n = Number(value);

  if (!Number.isFinite(n)) {
    return '—';
  }

  return `₹${formatCompact(n)}`;
}

function smartLabel(value: any) {
  if (
    value === undefined ||
    value === null ||
    value === ''
  ) {
    return '—';
  }

  return String(value)
    .replaceAll('_', ' ')
    .toUpperCase();
}

const styles = {
  sectionTitle: {
    color: '#a7b1bf',
    fontSize: 11,
    fontWeight: '800' as const,
    letterSpacing: 0.8,
  },

  helper: {
    color: '#727d8b',
    fontSize: 11,
    paddingHorizontal: 3,
  },

  columnHeader: {
    color: '#788493',
    fontSize: 9,
    fontWeight: '900' as const,
    letterSpacing: 0.7,
  },

  mutedTiny: {
    color: '#778290',
    fontSize: 9,
    marginTop: 4,
  },

  metricSecondary: {
    color: '#778290',
    fontSize: 9,
    marginTop: 4,
    maxWidth: 150,
  },
};
