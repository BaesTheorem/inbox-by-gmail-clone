package com.baestheorem.inbox.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import com.baestheorem.inbox.data.SwipeActionKind
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Two-zone swipe row: a short drag arms one action, dragging most of the way
 * across arms the full-swipe action. The reveal color and icon swap at the zone
 * boundary with a haptic tick, in the spirit of the original Inbox swipe
 * reveals.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun SwipeableRow(
    leadingShort: SwipeActionKind,
    leadingLong: SwipeActionKind,
    trailingShort: SwipeActionKind,
    trailingLong: SwipeActionKind,
    onTap: () -> Unit,
    onLongPress: () -> Unit = {},
    perform: (SwipeActionKind) -> Unit,
    content: @Composable () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val haptics = LocalHapticFeedback.current
    val density = LocalDensity.current
    val screenWidthPx = with(density) { LocalConfiguration.current.screenWidthDp.dp.toPx() }
    val shortThreshold = with(density) { 76.dp.toPx() }
    val longThreshold = screenWidthPx * 0.52f

    val offset = remember { Animatable(0f) }
    var armed by remember { mutableStateOf<SwipeActionKind?>(null) }

    fun zoneFor(x: Float): SwipeActionKind? {
        if (x > 0) {
            if (x >= longThreshold && leadingLong != SwipeActionKind.NONE) return leadingLong
            if (x >= shortThreshold && leadingShort != SwipeActionKind.NONE) return leadingShort
            // long slot empty: a full drag still fires the short action
            if (x >= longThreshold && leadingShort != SwipeActionKind.NONE) return leadingShort
            return null
        }
        if (x < 0) {
            val ax = -x
            if (ax >= longThreshold && trailingLong != SwipeActionKind.NONE) return trailingLong
            if (ax >= shortThreshold && trailingShort != SwipeActionKind.NONE) return trailingShort
            if (ax >= longThreshold && trailingShort != SwipeActionKind.NONE) return trailingShort
            return null
        }
        return null
    }

    val pending = when {
        offset.value > 0 -> if (leadingShort != SwipeActionKind.NONE) leadingShort else leadingLong
        offset.value < 0 -> if (trailingShort != SwipeActionKind.NONE) trailingShort else trailingLong
        else -> null
    }
    val show = armed ?: pending

    Box(Modifier.fillMaxWidth()) {
        if (offset.value != 0f && show != null && show != SwipeActionKind.NONE) {
            Box(
                Modifier
                    .matchParentSize()
                    .background(Color(show.color).copy(alpha = if (armed != null) 1f else 0.55f)),
                contentAlignment = if (offset.value > 0) Alignment.CenterStart else Alignment.CenterEnd,
            ) {
                MIcon(show.icon, size = 24, color = Color.White, modifier = Modifier.padding(horizontal = 26.dp))
            }
        }
        Box(
            Modifier
                .fillMaxWidth()
                .offset { IntOffset(offset.value.roundToInt(), 0) }
                .pointerInput(leadingShort, leadingLong, trailingShort, trailingLong) {
                    detectHorizontalDragGestures(
                        onDragStart = { armed = null },
                        onHorizontalDrag = { change, delta ->
                            change.consume()
                            var x = offset.value + delta
                            if (x > 0 && leadingShort == SwipeActionKind.NONE &&
                                leadingLong == SwipeActionKind.NONE
                            ) x = 0f
                            if (x < 0 && trailingShort == SwipeActionKind.NONE &&
                                trailingLong == SwipeActionKind.NONE
                            ) x = 0f
                            scope.launch { offset.snapTo(x) }
                            val next = zoneFor(x)
                            if (next != armed) {
                                armed = next
                                if (next != null) {
                                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                                }
                            }
                        },
                        onDragCancel = {
                            armed = null
                            scope.launch { offset.animateTo(0f, tween(180)) }
                        },
                        onDragEnd = {
                            val fired = armed
                            armed = null
                            if (fired == null || fired == SwipeActionKind.NONE) {
                                scope.launch { offset.animateTo(0f, tween(180)) }
                            } else if (fired.removesRow) {
                                // Slide off, then let the store's removal take the row
                                val dir = if (offset.value > 0) 1f else -1f
                                scope.launch {
                                    offset.animateTo(dir * screenWidthPx, tween(150))
                                    perform(fired)
                                    offset.snapTo(0f)
                                }
                            } else {
                                scope.launch { offset.animateTo(0f, tween(220)) }
                                perform(fired)
                            }
                        },
                    )
                }
                .combinedClickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null,
                    onClick = {
                        if (abs(offset.value) > 1f) scope.launch { offset.animateTo(0f, tween(150)) }
                        else onTap()
                    },
                    onLongClick = onLongPress,
                )
        ) {
            content()
        }
    }
}
