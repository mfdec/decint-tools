package tools.decint.app

import android.app.Activity
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClient.BillingResponseCode
import com.android.billingclient.api.BillingClient.ProductType
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.PurchasesUpdatedListener
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import org.json.JSONArray
import org.json.JSONObject

/**
 * Google Play Billing for the plans sold in the app.
 *
 * The site's pricing page drives it through the page bridge (`DecintNative`,
 * see frontend/lib/playBilling.ts). Each request carries an `id`, and the reply
 * echoes it:
 *
 *   play:products  {products:[ids]}           → {items:[{productId, basePlanId, price, period}]}
 *   play:subscribe {productId, basePlanId,
 *                   accountRef, replaceToken?} → {status: purchased|pending|cancelled|owned|error,
 *                                                  purchaseToken?}
 *   play:owned     {}                          → {items:[{purchaseToken, productId, acknowledged, accountRef}]}
 *
 * The app grants nothing itself. The page sends the purchase token to the
 * server, which checks it with Google, grants the plan and acknowledges it.
 * Google's own notifications to the server cover any purchase whose report is lost.
 */
class PlayBilling(private val activity: Activity) : PurchasesUpdatedListener {

    fun interface Reply {
        fun send(message: JSONObject)
    }

    private val client: BillingClient = BillingClient.newBuilder(activity.applicationContext)
        .setListener(this)
        .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
        .enableAutoServiceReconnection()
        .build()

    private val details = mutableMapOf<String, ProductDetails>()
    private val waiting = mutableListOf<(String?) -> Unit>()
    private var connecting = false

    /** The page waiting on the purchase sheet, and its request id. */
    private var purchaseReply: Reply? = null
    private var purchaseId: String = ""

    fun handle(msg: JSONObject, reply: Reply) {
        val id = msg.optString("id")
        val answer = Reply { reply.send(it.put("id", id).put("type", msg.optString("type"))) }
        whenReady { problem ->
            if (problem != null) {
                answer.send(JSONObject().put("ok", false).put("status", "error").put("error", problem))
                return@whenReady
            }
            when (msg.optString("type")) {
                "play:products" -> products(msg.optJSONArray("products"), answer)
                "play:subscribe" -> subscribe(msg, id, answer)
                "play:owned" -> owned(answer)
                else -> answer.send(JSONObject().put("ok", false).put("error", "unknown request"))
            }
        }
    }

    fun close() {
        if (client.isReady) client.endConnection()
    }

    // ── connection ──

    private fun whenReady(then: (String?) -> Unit) {
        if (client.isReady) return then(null)
        waiting += then
        if (connecting) return
        connecting = true
        client.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) = activity.runOnUiThread {
                connecting = false
                val problem = if (result.responseCode == BillingResponseCode.OK) null else describe(result)
                val ready = waiting.toList()
                waiting.clear()
                ready.forEach { it(problem) }
            }

            override fun onBillingServiceDisconnected() {
                // enableAutoServiceReconnection() reconnects on the next call.
            }
        })
    }

    // ── requests ──

    private fun products(ids: JSONArray?, reply: Reply) {
        val wanted = (0 until (ids?.length() ?: 0)).map { ids!!.getString(it) }.filter { it.isNotBlank() }
        if (wanted.isEmpty()) {
            reply.send(JSONObject().put("ok", true).put("items", JSONArray()))
            return
        }
        val params = QueryProductDetailsParams.newBuilder()
            .setProductList(wanted.map {
                QueryProductDetailsParams.Product.newBuilder().setProductId(it).setProductType(ProductType.SUBS).build()
            })
            .build()
        client.queryProductDetailsAsync(params) { result, queried ->
            activity.runOnUiThread {
                if (result.responseCode != BillingResponseCode.OK) {
                    reply.send(JSONObject().put("ok", false).put("error", describe(result)))
                    return@runOnUiThread
                }
                val items = JSONArray()
                for (product in queried.productDetailsList) {
                    details[product.productId] = product
                    // Base plans only (offerId == null): introductory offers aren't sold yet.
                    for (offer in product.subscriptionOfferDetails.orEmpty().filter { it.offerId == null }) {
                        val phase = offer.pricingPhases.pricingPhaseList.lastOrNull() ?: continue
                        items.put(
                            JSONObject()
                                .put("productId", product.productId)
                                .put("basePlanId", offer.basePlanId)
                                .put("price", phase.formattedPrice)
                                .put("period", phase.billingPeriod),
                        )
                    }
                }
                reply.send(JSONObject().put("ok", true).put("items", items))
            }
        }
    }

    private fun subscribe(msg: JSONObject, id: String, reply: Reply) {
        val product = details[msg.optString("productId")]
        val offer = product?.subscriptionOfferDetails.orEmpty().firstOrNull {
            it.basePlanId == msg.optString("basePlanId") && it.offerId == null
        }
        val accountRef = msg.optString("accountRef")
        if (product == null || offer == null || accountRef.isBlank()) {
            reply.send(JSONObject().put("status", "error").put("error", "That plan isn't available."))
            return
        }

        val flow = BillingFlowParams.newBuilder()
            .setProductDetailsParamsList(
                listOf(
                    BillingFlowParams.ProductDetailsParams.newBuilder()
                        .setProductDetails(product)
                        .setOfferToken(offer.offerToken)
                        .build(),
                ),
            )
            // Ties the purchase to the DECINT account; the server refuses it otherwise.
            .setObfuscatedAccountId(accountRef)
        msg.optString("replaceToken").takeIf { it.isNotBlank() }?.let { old ->
            // A plan or period change replaces the current subscription, crediting
            // its unused time, instead of adding a second one.
            flow.setSubscriptionUpdateParams(
                BillingFlowParams.SubscriptionUpdateParams.newBuilder()
                    .setOldPurchaseToken(old)
                    .setSubscriptionReplacementMode(
                        BillingFlowParams.SubscriptionUpdateParams.ReplacementMode.WITH_TIME_PRORATION,
                    )
                    .build(),
            )
        }

        // Only one purchase sheet at a time; an earlier request that never
        // finished is answered so its page doesn't wait forever.
        purchaseReply?.send(JSONObject().put("id", purchaseId).put("status", "cancelled"))
        purchaseReply = reply
        purchaseId = id
        val result = client.launchBillingFlow(activity, flow.build())
        if (result.responseCode != BillingResponseCode.OK) {
            finishPurchase(JSONObject().put("status", "error").put("error", describe(result)))
        }
    }

    private fun owned(reply: Reply) {
        val params = QueryPurchasesParams.newBuilder().setProductType(ProductType.SUBS).build()
        client.queryPurchasesAsync(params) { result, purchases ->
            activity.runOnUiThread {
                if (result.responseCode != BillingResponseCode.OK) {
                    reply.send(JSONObject().put("ok", false).put("error", describe(result)))
                    return@runOnUiThread
                }
                val items = JSONArray()
                purchases.filter { it.purchaseState == Purchase.PurchaseState.PURCHASED }.forEach {
                    items.put(
                        JSONObject()
                            .put("purchaseToken", it.purchaseToken)
                            .put("productId", it.products.firstOrNull().orEmpty())
                            .put("acknowledged", it.isAcknowledged)
                            .put("accountRef", it.accountIdentifiers?.obfuscatedAccountId.orEmpty()),
                    )
                }
                reply.send(JSONObject().put("ok", true).put("items", items))
            }
        }
    }

    // ── the purchase sheet's outcome ──

    override fun onPurchasesUpdated(result: BillingResult, purchases: List<Purchase>?) {
        activity.runOnUiThread {
            val purchase = purchases?.firstOrNull()
            val reply = when (result.responseCode) {
                BillingResponseCode.OK -> if (purchase == null) {
                    JSONObject().put("status", "error").put("error", "No purchase was returned.")
                } else {
                    JSONObject()
                        .put(
                            "status",
                            if (purchase.purchaseState == Purchase.PurchaseState.PURCHASED) "purchased" else "pending",
                        )
                        .put("purchaseToken", purchase.purchaseToken)
                        .put("productId", purchase.products.firstOrNull().orEmpty())
                }
                BillingResponseCode.USER_CANCELED -> JSONObject().put("status", "cancelled")
                BillingResponseCode.ITEM_ALREADY_OWNED -> JSONObject().put("status", "owned")
                else -> JSONObject().put("status", "error").put("error", describe(result))
            }
            finishPurchase(reply)
        }
    }

    private fun finishPurchase(message: JSONObject) {
        val reply = purchaseReply ?: return
        purchaseReply = null
        reply.send(message)
    }

    private fun describe(result: BillingResult): String =
        result.debugMessage.ifBlank { "Google Play billing error ${result.responseCode}" }
}
