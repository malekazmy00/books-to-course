package com.book2course.learner.ui.concepts

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.book2course.learner.R
import com.book2course.learner.data.LearnerRepository
import com.book2course.learner.data.Loadable
import com.book2course.learner.data.api.ConceptCard
import com.book2course.learner.data.api.ConceptList
import com.book2course.learner.ui.common.EmptyState
import com.book2course.learner.ui.common.ErrorBanner
import com.book2course.learner.ui.common.LearnerScaffold
import com.book2course.learner.ui.common.RefreshBar
import com.book2course.learner.ui.common.VoiceChip
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.stateIn

@OptIn(ExperimentalCoroutinesApi::class)
class ConceptsViewModel(courseId: String, repository: LearnerRepository) : ViewModel() {
    private val trigger = MutableStateFlow(0)
    val concepts: StateFlow<Loadable<ConceptList>> = trigger
        .flatMapLatest { repository.concepts(courseId) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, Loadable(refreshing = true))

    fun refresh() {
        trigger.value++
    }
}

fun filterConcepts(cards: List<ConceptCard>, query: String): List<ConceptCard> {
    val q = query.trim()
    if (q.isEmpty()) return cards
    return cards.filter { c ->
        c.term.contains(q, ignoreCase = true) || c.termAr?.contains(q) == true || c.gloss?.contains(q, ignoreCase = true) == true
    }
}

@Composable
fun ConceptsScreen(
    concepts: Loadable<ConceptList>,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    onOpenEpisode: (Int) -> Unit,
) {
    var query by rememberSaveable { mutableStateOf("") }
    LearnerScaffold(title = stringResource(R.string.concepts_title), onBack = onBack) {
        RefreshBar(concepts.refreshing && concepts.data != null)
        concepts.error?.let { ErrorBanner(it, concepts.data != null, onRefresh) }
        val data = concepts.data
        if (data == null || data.status != "ready") {
            if (!concepts.refreshing) EmptyState(stringResource(R.string.concepts_pending))
            return@LearnerScaffold
        }
        OutlinedTextField(
            value = query, onValueChange = { query = it }, singleLine = true,
            label = { Text(stringResource(R.string.concepts_search)) },
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        )
        if (data.language != "ar") {
            Text(stringResource(R.string.concepts_language_note), Modifier.padding(horizontal = 16.dp),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxSize()) {
            items(filterConcepts(data.concepts, query), key = { it.conceptId }) { card -> ConceptCardView(card, data.language, onOpenEpisode) }
        }
    }
}

@Composable
private fun ConceptCardView(card: ConceptCard, language: String, onOpenEpisode: (Int) -> Unit) {
    var expanded by rememberSaveable(card.conceptId) { mutableStateOf(false) }
    val expandedText = stringResource(if (expanded) R.string.state_expanded else R.string.state_collapsed)
    val sourceDirection = if (language == "ar") LayoutDirection.Rtl else LayoutDirection.Ltr
    Card(Modifier.fillMaxWidth().animateContentSize()) {
        Column(
            Modifier.fillMaxWidth().heightIn(min = 56.dp).clickable { expanded = !expanded }
                .semantics { stateDescription = expandedText }
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            card.termAr?.let { Text(it, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() }) }
            CompositionLocalProvider(LocalLayoutDirection provides sourceDirection) {
                Text(card.term, style = if (card.termAr == null) MaterialTheme.typography.titleLarge else MaterialTheme.typography.titleMedium,
                    modifier = if (card.termAr == null) Modifier.semantics { heading() } else Modifier)
            }
            card.firstEpisode?.let { ep ->
                TextButton(onClick = { onOpenEpisode(ep) }, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(stringResource(R.string.concept_first_episode, ep))
                }
            }
            if (expanded) {
                card.gloss?.let {
                    VoiceChip("course")
                    CompositionLocalProvider(LocalLayoutDirection provides sourceDirection) { Text(it, style = MaterialTheme.typography.bodyLarge) }
                }
                if (card.authorQuotes.isNotEmpty()) {
                    VoiceChip("author")
                    card.authorQuotes.forEach { q ->
                        CompositionLocalProvider(LocalLayoutDirection provides sourceDirection) {
                            Text("“${q.quote}”", style = MaterialTheme.typography.bodyLarge, fontStyle = FontStyle.Italic)
                        }
                        if (q.pages.isNotEmpty()) Text(stringResource(R.string.pages, q.pages.joinToString("، ")), style = MaterialTheme.typography.labelSmall)
                    }
                }
                card.externalNotes.forEach { note ->
                    VoiceChip("external")
                    CompositionLocalProvider(LocalLayoutDirection provides sourceDirection) {
                        Text(note.statement, style = MaterialTheme.typography.bodyLarge)
                        note.urls.forEach { Text(it, style = MaterialTheme.typography.labelSmall) }
                    }
                }
                if (card.relations.isNotEmpty()) {
                    Text(stringResource(R.string.concept_related), style = MaterialTheme.typography.titleSmall)
                    card.relations.forEach { r -> Text("${relationLabel(r.relation)}: ${r.term}", style = MaterialTheme.typography.bodyMedium) }
                }
            }
        }
    }
}

@Composable
private fun relationLabel(relation: String): String = when (relation) {
    "prerequisite" -> stringResource(R.string.relation_prerequisite)
    "part_of" -> stringResource(R.string.relation_part_of)
    "contrasts_with" -> stringResource(R.string.relation_contrasts_with)
    else -> stringResource(R.string.relation_related)
}
