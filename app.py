import React, { useState, useEffect, useMemo } from 'react';
import { initializeApp } from 'firebase/app';
import { getAuth, signInAnonymously, onAuthStateChanged, signInWithCustomToken } from 'firebase/auth';
import { getFirestore, collection, doc, setDoc, addDoc, onSnapshot, query, limit, orderBy } from 'firebase/firestore';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';
import { Shield, TrendingUp, Activity, History, Settings, RefreshCcw, Zap, Target, Lock, Wifi, WifiOff, AlertCircle } from 'lucide-react';

// --- ADIM 1: BULUT DEFTERİ (FIREBASE) BAĞLANTISI ---
const firebaseConfig = JSON.parse(window.__firebase_config || '{}');
const appId = typeof window.__app_id !== 'undefined' ? window.__app_id : 'quant-lab-v7';
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);

// --- YARDIMCI MATEMATİK FONKSİYONU ---
const calculateEMA = (data, period) => {
  if (data.length < period) return data;
  const k = 2 / (period + 1);
  let ema = [data[0]];
  for (let i = 1; i < data.length; i++) {
    ema.push(data[i] * k + ema[i - 1] * (1 - k));
  }
  return ema;
};

export default function App() {
  // --- DURUM DEĞİŞKENLERİ ---
  const [user, setUser] = useState(null);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [price, setPrice] = useState(0);
  const [history, setHistory] = useState([]);
  const [currentPos, setCurrentPos] = useState(null);
  const [isBotRunning, setIsBotRunning] = useState(true);
  const [lastCheck, setLastCheck] = useState(null);
  const [dbStatus, setDbStatus] = useState('connecting'); // 'connecting', 'online', 'error'
  const [settings] = useState({ emaF: 9, emaS: 21, adxT: 15, tp: 4.5 });

  // --- BULUTTA OTURUM AÇMA ---
  useEffect(() => {
    const initAuth = async () => {
      try {
        if (typeof window.__initial_auth_token !== 'undefined' && window.__initial_auth_token) {
          await signInWithCustomToken(auth, window.__initial_auth_token);
        } else {
          await signInAnonymously(auth);
        }
        setDbStatus('online');
      } catch (err) {
        console.error("Auth Error:", err);
        setDbStatus('error');
      }
    };
    initAuth();
    const unsubscribe = onAuthStateChanged(auth, (u) => setUser(u));
    return () => unsubscribe();
  }, []);

  // --- VERİTABANINI CANLI İZLE ---
  useEffect(() => {
    if (!user) return;

    // Geçmiş işlemleri izle
    const tradesRef = collection(db, 'artifacts', appId, 'users', user.uid, 'trade_history');
    const q = query(tradesRef, orderBy('timestamp', 'desc'), limit(20));
    const unsubscribeHistory = onSnapshot(q, (snapshot) => {
      const data = snapshot.docs.map(doc => ({ id: doc.id, ...doc.data() }));
      setHistory(data);
    }, (err) => {
      console.error("History Error:", err);
      setDbStatus('error');
    });

    // Aktif pozisyonu izle
    const activePosRef = doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current');
    const unsubscribeActive = onSnapshot(activePosRef, (snapshot) => {
      if (snapshot.exists()) {
        const data = snapshot.data();
        setCurrentPos(Object.keys(data).length > 0 ? data : null);
      } else {
        setCurrentPos(null);
      }
    }, (err) => {
      console.error("Active Pos Error:", err);
    });

    return () => {
      unsubscribeHistory();
      unsubscribeActive();
    };
  }, [user]);

  // --- ANA DÖNGÜ (HER 10 SANİYEDE BİR) ---
  useEffect(() => {
    const mainLoop = async () => {
      try {
        const priceRes = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`);
        const priceData = await priceRes.json();
        if (priceData.price) {
          const currentPrice = parseFloat(priceData.price);
          setPrice(currentPrice);
          setLastCheck(new Date().toLocaleTimeString());
          if (isBotRunning && user) await runTradingLogic(currentPrice);
        }
      } catch (e) {
        console.error("Price Fetch Error:", e);
      }
    };

    const interval = setInterval(mainLoop, 10000);
    return () => clearInterval(interval);
  }, [symbol, currentPos, isBotRunning, user]);

  // --- İŞLEM MANTIĞI ---
  const runTradingLogic = async (p) => {
    if (!user) return;

    if (currentPos) {
      // Pozisyon Kapatma Kontrolü
      let exit = false;
      let result = "";
      if (currentPos.type === 'LONG') {
        if (p <= currentPos.sl) { exit = true; result = "LOSS"; }
        else if (p >= currentPos.tp) { exit = true; result = "WIN"; }
      } else {
        if (p >= currentPos.sl) { exit = true; result = "LOSS"; }
        else if (p <= currentPos.tp) { exit = true; result = "WIN"; }
      }

      if (exit) {
        const pnl = currentPos.type === 'LONG' 
          ? ((p - currentPos.entry) / currentPos.entry) * 100 
          : ((currentPos.entry - p) / currentPos.entry) * 100;

        await addDoc(collection(db, 'artifacts', appId, 'users', user.uid, 'trade_history'), {
          symbol, type: currentPos.type, entry: currentPos.entry, exit: p,
          pnl: pnl.toFixed(2), result, timestamp: Date.now()
        });
        await setDoc(doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current'), {});
      }
      return;
    }

    // Yeni Fırsat Arama
    try {
      const klinesRes = await fetch(`https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=15m&limit=100`);
      const klines = await klinesRes.json();
      const closes = klines.map(k => parseFloat(k[4]));
      const highs = klines.map(k => parseFloat(k[2]));
      const lows = klines.map(k => parseFloat(k[3]));

      const emaF = calculateEMA(closes, settings.emaF);
      const emaS = calculateEMA(closes, settings.emaS);
      
      const lastF = emaF[emaF.length - 1];
      const lastS = emaS[emaS.length - 1];
      const prevF = emaF[emaF.length - 2];
      const prevS = emaS[emaS.length - 2];
      const atr = (highs[highs.length-1] - lows[lows.length-1]);

      if (prevF <= prevS && lastF > lastS) {
        await setDoc(doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current'), {
          type: 'LONG', entry: p, sl: p - (atr * 2), tp: p + (atr * settings.tp), timestamp: Date.now(), symbol
        });
      } else if (prevF >= prevS && lastF < lastS) {
        await setDoc(doc(db, 'artifacts', appId, 'users', user.uid, 'active_position', 'current'), {
          type: 'SHORT', entry: p, sl: p + (atr * 2), tp: p - (atr * settings.tp), timestamp: Date.now(), symbol
        });
      }
    } catch (e) {
      console.error("Logic Error:", e);
    }
  };

  const winRate = useMemo(() => {
    if (history.length === 0) return 0;
    const wins = history.filter(h => h.result === 'WIN').length;
    return (wins / history.length) * 100;
  }, [history]);

  const totalPnL = useMemo(() => {
    return history.reduce((acc, curr) => acc + parseFloat(curr.pnl), 0).toFixed(2);
  }, [history]);

  return (
    <div className="min-h-screen bg-[#0E1117] text-white p-4 font-sans selection:bg-[#00FF41]/30">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center mb-8 gap-4 bg-[#1A1D23] p-6 rounded-3xl border border-gray-800 shadow-2xl">
        <div className="flex items-center gap-4">
          <div className="bg-[#00FF41]/10 p-3 rounded-2xl">
            <Shield size={32} className="text-[#00FF41]" />
          </div>
          <div>
            <h1 className="text-2xl font-black tracking-tighter text-white">CLOUD SENTINEL <span className="text-[#00FF41]">V7.0</span></h1>
            <div className="flex items-center gap-3 text-xs">
              <div className="flex items-center gap-1.5 text-gray-500">
                <div className={`w-2 h-2 rounded-full ${isBotRunning ? 'bg-[#00FF41] animate-pulse' : 'bg-gray-600'}`}></div>
                OTOPİLOT {isBotRunning ? 'AKTİF' : 'DURDURULDU'}
              </div>
              <div className={`flex items-center gap-1.5 ${dbStatus === 'online' ? 'text-blue-400' : 'text-yellow-500'}`}>
                {dbStatus === 'online' ? <Wifi size={14} /> : <WifiOff size={14} />}
                BULUT: {dbStatus === 'online' ? 'BAĞLI' : 'BAĞLANTI YOK'}
              </div>
            </div>
          </div>
        </div>

        <div className="flex gap-4 items-center">
          <div className="text-right mr-4">
            <div className="text-[10px] text-gray-500 uppercase font-bold tracking-widest">Canlı {symbol}</div>
            <div className="text-2xl font-mono font-black text-[#00FF41]">{price.toLocaleString()} $</div>
          </div>
          <button 
            onClick={() => setIsBotRunning(!isBotRunning)}
            className={`px-6 py-2 rounded-2xl font-bold transition-all flex items-center gap-2 ${isBotRunning ? 'bg-red-500/10 text-red-500 hover:bg-red-500/20' : 'bg-[#00FF41]/10 text-[#00FF41] hover:bg-[#00FF41]/20'}`}
          >
            {isBotRunning ? <><Lock size={18}/> DURDUR</> : <><Zap size={18}/> BAŞLAT</>}
          </button>
        </div>
      </div>

      <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-8">
          {dbStatus === 'error' && (
            <div className="bg-red-500/10 border border-red-500/50 p-4 rounded-2xl flex items-center gap-3 text-red-500 text-sm font-bold">
              <AlertCircle size={20} /> Firebase yapılandırması eksik veya kurallar hatalı. Lütfen "Rules" sekmesini kontrol et!
            </div>
          )}
          
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-[#1A1D23] p-6 rounded-3xl border border-gray-800">
              <div className="text-gray-500 text-[10px] font-black uppercase mb-1 tracking-widest">Başarı Oranı</div>
              <div className="text-3xl font-black text-[#00FF41]">{winRate.toFixed(1)}%</div>
            </div>
            <div className="bg-[#1A1D23] p-6 rounded-3xl border border-gray-800">
              <div className="text-gray-500 text-[10px] font-black uppercase mb-1 tracking-widest">Toplam Kar (%)</div>
              <div className={`text-3xl font-black ${parseFloat(totalPnL) >= 0 ? 'text-[#00FF41]' : 'text-red-500'}`}>%{totalPnL}</div>
            </div>
            <div className="bg-[#1A1D23] p-6 rounded-3xl border border-gray-800">
              <div className="text-gray-500 text-[10px] font-black uppercase mb-1 tracking-widest">Bulut Kaydı</div>
              <div className="text-3xl font-black text-white">{history.length}</div>
            </div>
          </div>

          <div className="bg-[#1A1D23] rounded-3xl border-2 border-[#00FF41]/20 p-8 relative overflow-hidden shadow-2xl">
            <div className="flex justify-between items-center mb-8">
              <h2 className="text-xl font-black flex items-center gap-3">
                <Activity className="text-[#00FF41]" /> CANLI POZİSYON DURUMU
              </h2>
              <div className="text-[10px] text-gray-500 font-mono italic">Güncelleme: {lastCheck}</div>
            </div>

            {currentPos ? (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Yön</div>
                  <div className={`text-2xl font-black ${currentPos.type === 'LONG' ? 'text-[#00FF41]' : 'text-red-500'}`}>{currentPos.type}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Giriş</div>
                  <div className="text-2xl font-mono font-bold text-white">{currentPos.entry.toLocaleString()}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Hedef</div>
                  <div className="text-2xl font-mono font-bold text-[#00FF41]">{currentPos.tp.toLocaleString()}</div>
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-gray-500 font-bold uppercase">Anlık Kar</div>
                  <div className={`text-3xl font-black ${((price - currentPos.entry) * (currentPos.type === 'LONG' ? 1 : -1)) >= 0 ? 'text-[#00FF41]' : 'text-red-500'}`}>
                    %{currentPos.type === 'LONG' 
                      ? (((price - currentPos.entry)/currentPos.entry)*100).toFixed(2)
                      : (((currentPos.entry - price)/currentPos.entry)*100).toFixed(2)}
                  </div>
                </div>
              </div>
            ) : (
              <div className="py-12 text-center text-gray-600">
                <RefreshCcw size={32} className="mx-auto mb-4 opacity-20 animate-spin" />
                <p className="text-lg font-bold mb-2">İşlem bekleniyor...</p>
                <p className="text-xs opacity-50">Sistem Binance verilerini tarıyor ve bulut onayı bekliyor.</p>
              </div>
            )}
          </div>

          <div className="bg-[#1A1D23] rounded-3xl border border-gray-800 overflow-hidden shadow-xl">
            <div className="p-6 border-b border-gray-800 flex justify-between items-center bg-[#1A1D23]">
              <h2 className="font-black flex items-center gap-3"><History /> İŞLEM GÜNLÜĞÜ</h2>
              <span className="text-[10px] bg-[#00FF41]/10 text-[#00FF41] px-3 py-1 rounded-full font-bold uppercase tracking-widest">Canlı Bulut Verisi</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="bg-[#0E1117]/50 text-gray-500 text-[10px] font-black uppercase tracking-widest">
                  <tr>
                    <th className="p-6">Zaman</th>
                    <th className="p-6">Varlık</th>
                    <th className="p-6">Yön</th>
                    <th className="p-6">PnL (%)</th>
                    <th className="p-6">Sonuç</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/50">
                  {history.map((t) => (
                    <tr key={t.id} className="hover:bg-white/[0.02] transition-colors group">
                      <td className="p-6 text-xs text-gray-400 font-mono">{new Date(t.timestamp).toLocaleTimeString()}</td>
                      <td className="p-6 text-sm font-bold text-white">{t.symbol}</td>
                      <td className={`p-6 text-sm font-black ${t.type === 'LONG' ? 'text-[#00FF41]' : 'text-red-500'}`}>{t.type}</td>
                      <td className={`p-6 text-lg font-mono font-black ${parseFloat(t.pnl) >= 0 ? 'text-[#00FF41]' : 'text-red-500'}`}>%{t.pnl}</td>
                      <td className="p-6">
                        <div className={`inline-flex px-3 py-1 rounded-lg text-[10px] font-black uppercase ${t.result === 'WIN' ? 'bg-[#00FF41]/10 text-[#00FF41]' : 'bg-red-500/10 text-red-500'}`}>
                          {t.result}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="space-y-8">
          <div className="bg-[#1A1D23] rounded-3xl border border-gray-800 p-8 shadow-xl">
            <h2 className="font-black mb-6 flex items-center gap-3"><Settings className="text-[#00FF41]" /> KONFİGÜRASYON</h2>
            <div className="space-y-6">
              <div className="space-y-2">
                <label className="text-[10px] text-gray-500 font-black uppercase tracking-widest">Varlık İzleme</label>
                <select 
                  className="w-full bg-[#0E1117] border border-gray-800 rounded-2xl p-4 text-sm font-bold focus:ring-2 focus:ring-[#00FF41] outline-none"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                >
                  <option value="BTCUSDT">BITCOIN</option>
                  <option value="ETHUSDT">ETHEREUM</option>
                  <option value="SOLUSDT">SOLANA</option>
                </select>
              </div>

              <div className="p-6 bg-[#0E1117] rounded-2xl border border-gray-800">
                <h3 className="text-[10px] font-black text-gray-500 uppercase mb-4 tracking-widest flex items-center gap-2">
                  <Target size={14} className="text-[#00FF41]" /> Teknik Ayarlar
                </h3>
                <div className="space-y-3">
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-400">Trend (EMA)</span>
                    <span className="text-[#00FF41] font-bold">9 / 21</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-400">Hız (ADX)</span>
                    <span className="text-[#00FF41] font-bold">&gt; 15</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-gray-400">Hedef (RR)</span>
                    <span className="text-[#00FF41] font-bold">1 : 2.2</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-[#1A1D23] rounded-3xl border border-gray-800 p-8">
             <h2 className="text-sm font-black mb-4 flex items-center gap-3 text-blue-400"><Wifi size={18} /> BULUT KURULUMU</h2>
             <ul className="text-[10px] text-gray-500 space-y-3 leading-relaxed">
               <li>1. Firebase Console'dan <b>Firestore</b> oluştur.</li>
               <li>2. <b>Anonymous Auth</b>'u etkinleştir.</li>
               <li>3. Rules sekmesini <b>"read/write if true"</b> yap.</li>
               <li>4. Railway'e Deploy et ve sayfayı açık bırak.</li>
             </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
