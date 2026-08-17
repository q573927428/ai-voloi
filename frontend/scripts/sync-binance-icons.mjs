// ==================== 币安合约图标同步脚本 ====================
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, extname, resolve } from 'node:path'
import process from 'node:process'

const BINANCE_EXCHANGE_INFO_URL = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
const BINANCE_LOGO_API_URL = 'https://www.binance.com/bapi/apex/v1/public/apex/marketing/futures/asset/logo'
const BINANCE_FUTURES_PAGE_URL = 'https://www.binance.com/zh-CN/futures/'
const BINANCE_ICON_HOSTS = new Set(['bin.bnbstatic.com', 'public.bnbstatic.com'])
const DEFAULT_MANIFEST_PATH = resolve('src/assets/binance-coin-icons.json')
const DEFAULT_ICON_DIR = resolve('public/coin-icons')
const SUPPORTED_CONTRACT_TYPES = new Set(['PERPETUAL', 'TRADIFI_PERPETUAL'])

/** 解析同步参数；默认只更新 JSON，--download 会额外保存图片文件。 */
function parseOptions(argv) {
  const options = {
    download: false,
    output: DEFAULT_MANIFEST_PATH,
    iconDir: DEFAULT_ICON_DIR,
    dryRun: false,
    help: false,
  }

  for (const argument of argv) {
    if (argument === '--download') options.download = true
    else if (argument === '--dry-run') options.dryRun = true
    else if (argument === '--help') options.help = true
    else if (argument.startsWith('--output=')) options.output = resolve(argument.slice('--output='.length))
    else if (argument.startsWith('--icon-dir=')) options.iconDir = resolve(argument.slice('--icon-dir='.length))
    else throw new Error(`未知参数：${argument}`)
  }
  return options
}

function printHelp() {
  console.log(`币安合约图标同步脚本

用法：
  npm run sync:coin-icons
  npm run sync:coin-icons -- --download
  npm run sync:coin-icons -- --dry-run

选项：
  --download         将图片下载到 public/coin-icons，清单优先使用本地 URL
  --dry-run          请求并校验最新数据，但不写入文件
  --output=PATH      自定义清单输出路径
  --icon-dir=PATH    自定义图标下载目录`)
}

/** 读取上一版清单，用于 Binance 临时缺少某项时保留已验证的旧地址。 */
async function readPreviousManifest(path) {
  if (!existsSync(path)) return { assets: {} }
  try {
    const manifest = JSON.parse(await readFile(path, 'utf8'))
    return manifest && typeof manifest.assets === 'object' ? manifest : { assets: {} }
  } catch (error) {
    throw new Error(`无法读取现有图标清单 ${path}：${error.message}`)
  }
}

/** 请求 Binance JSON，并提供包含 HTTP 状态的错误信息。 */
async function fetchJson(url) {
  const response = await fetch(url, {
    headers: {
      Accept: 'application/json',
      'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
      'User-Agent': 'Mozilla/5.0 ai-voloi-icon-sync/1.0',
    },
  })
  if (!response.ok) throw new Error(`${url} 请求失败：HTTP ${response.status}`)
  return response.json()
}

/** 从官方 exchangeInfo 获取当前正在交易的全部 U 本位永续基础资产。 */
async function fetchContractAssets() {
  const data = await fetchJson(BINANCE_EXCHANGE_INFO_URL)
  if (!Array.isArray(data.symbols)) throw new Error('exchangeInfo 响应缺少 symbols 数组')

  const assets = new Map()
  for (const item of data.symbols) {
    if (item.status !== 'TRADING' || !SUPPORTED_CONTRACT_TYPES.has(item.contractType)) continue
    const baseAsset = String(item.baseAsset || '').toUpperCase()
    const symbol = String(item.symbol || '').toUpperCase()
    if (!baseAsset || !symbol) continue

    const current = assets.get(baseAsset)
    const candidate = {
      baseAsset,
      symbol,
      symbols: [symbol],
      contractType: item.contractType,
      underlyingType: item.underlyingType || null,
      quoteAsset: item.quoteAsset || null,
    }
    if (!current) {
      assets.set(baseAsset, candidate)
      continue
    }

    current.symbols.push(symbol)
    // 页面链接优先选择本项目使用的 USDT 合约，避免同资产多计价时结果随机。
    if (current.quoteAsset !== 'USDT' && candidate.quoteAsset === 'USDT') {
      Object.assign(current, candidate, { symbols: current.symbols })
    }
  }

  return [...assets.values()]
    .map((asset) => ({ ...asset, symbols: [...new Set(asset.symbols)].sort() }))
    .sort((left, right) => left.baseAsset.localeCompare(right.baseAsset))
}

/** 仅接受 Binance 官方静态资源域名，避免异常响应把第三方地址写进前端。 */
function normalizeIconUrl(value) {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || !BINANCE_ICON_HOSTS.has(url.hostname)) return null
    if (!/^\/images?\//.test(url.pathname)) return null
    if (url.pathname.endsWith('/static/futures-header/default-icon.png')) return null
    return url.href
  } catch {
    return null
  }
}

/**
 * 获取币安交易页实际使用的完整资产 Logo 映射。
 * 该接口由 futures 页面调用，兼容旧加密币路径与新的股票、ETF symbol/logo 路径。
 */
async function fetchBinanceLogoMap() {
  const payload = await fetchJson(BINANCE_LOGO_API_URL)
  if (payload.code !== '000000' || !Array.isArray(payload.data)) {
    throw new Error('Binance futures asset/logo 响应格式不符合预期')
  }

  const logos = new Map()
  for (const item of payload.data) {
    const asset = String(item.asset || '').toUpperCase()
    const logo = normalizeIconUrl(item.logo)
    if (asset && logo) logos.set(asset, logo)
  }
  return logos
}

function safeFileName(baseAsset, iconUrl) {
  const sourceExtension = extname(new URL(iconUrl).pathname).toLowerCase()
  const extension = ['.png', '.jpg', '.jpeg', '.webp', '.svg'].includes(sourceExtension) ? sourceExtension : '.png'
  return `${baseAsset.replace(/[^\p{L}\p{N}_-]/gu, '_')}${extension}`
}

/** 下载远程图标；单个文件失败时保留远程 URL，不中断清单更新。 */
async function downloadIcons(assets, iconUrls, iconDir) {
  await mkdir(iconDir, { recursive: true })
  const localUrls = new Map()
  for (let index = 0; index < assets.length; index += 1) {
    const asset = assets[index]
    const iconUrl = iconUrls.get(asset.baseAsset)
    if (!iconUrl) continue
    try {
      const response = await fetch(iconUrl, { headers: { 'User-Agent': 'ai-voloi-icon-sync/1.0' } })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const fileName = safeFileName(asset.baseAsset, iconUrl)
      await writeFile(resolve(iconDir, fileName), Buffer.from(await response.arrayBuffer()))
      localUrls.set(asset.baseAsset, `/coin-icons/${fileName}`)
      console.log(`[下载 ${index + 1}/${assets.length}] ${asset.baseAsset}`)
    } catch (error) {
      console.warn(`${asset.baseAsset} 下载失败，继续使用远程地址：${error.message}`)
    }
  }
  return localUrls
}

/** 合并当前合约、官方 Logo 和旧清单，并保持资产键稳定排序。 */
function buildManifest(assets, officialLogos, previousAssets, localUrls) {
  const entries = {}
  const unresolved = []
  let officialCount = 0
  let retainedCount = 0

  for (const asset of assets) {
    const officialUrl = officialLogos.get(asset.baseAsset) || null
    const retainedUrl = officialUrl ? null : normalizeIconUrl(previousAssets[asset.baseAsset]?.iconUrl)
    const iconUrl = officialUrl || retainedUrl
    if (officialUrl) officialCount += 1
    else if (retainedUrl) retainedCount += 1
    else unresolved.push({ baseAsset: asset.baseAsset, symbol: asset.symbol })

    entries[asset.baseAsset] = {
      symbol: asset.symbol,
      symbols: asset.symbols,
      contractType: asset.contractType,
      underlyingType: asset.underlyingType,
      quoteAsset: asset.quoteAsset,
      iconUrl,
      localUrl: localUrls.get(asset.baseAsset) || previousAssets[asset.baseAsset]?.localUrl || null,
      source: officialUrl ? 'binance' : retainedUrl ? 'retained' : null,
    }
  }

  return {
    version: 1,
    updatedAt: new Date().toISOString(),
    source: {
      contracts: BINANCE_EXCHANGE_INFO_URL,
      logos: BINANCE_LOGO_API_URL,
    },
    pageTemplate: `${BINANCE_FUTURES_PAGE_URL}{symbol}`,
    assetCount: assets.length,
    resolvedCount: officialCount + retainedCount,
    officialCount,
    retainedCount,
    unresolvedCount: unresolved.length,
    unresolved,
    assets: entries,
  }
}

async function main() {
  const options = parseOptions(process.argv.slice(2))
  if (options.help) {
    printHelp()
    return
  }

  const [assets, officialLogos, previousManifest] = await Promise.all([
    fetchContractAssets(),
    fetchBinanceLogoMap(),
    readPreviousManifest(options.output),
  ])
  console.log(`Binance 当前 U 本位永续：${assets.length} 个基础资产；Logo 接口：${officialLogos.size} 条。`)

  const localUrls = options.download
    ? await downloadIcons(assets, officialLogos, options.iconDir)
    : new Map()
  const manifest = buildManifest(assets, officialLogos, previousManifest.assets, localUrls)

  if (options.dryRun) {
    console.log(`Dry run：匹配 ${manifest.officialCount}，保留旧值 ${manifest.retainedCount}，缺失 ${manifest.unresolvedCount}。`)
    return
  }

  await mkdir(dirname(options.output), { recursive: true })
  await writeFile(options.output, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  console.log(`已写入 ${options.output}：匹配 ${manifest.resolvedCount}/${manifest.assetCount}，缺失 ${manifest.unresolvedCount}。`)
  // Binance 的指数合约可能没有独立 Logo；保留清单并提示，不让日常手动同步因此失败。
  if (manifest.unresolvedCount > 0) {
    console.warn(`官方未提供 Logo：${manifest.unresolved.map((item) => item.symbol).join('、')}`)
  }
}

main().catch((error) => {
  console.error(`同步失败：${error.message}`)
  process.exitCode = 1
})
