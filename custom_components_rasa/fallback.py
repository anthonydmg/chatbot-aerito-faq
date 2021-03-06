import copy
import logging
from typing import Any, List, Type, Text, Dict, Union, Tuple, Optional

from sqlalchemy import true

from rasa.shared.constants import DEFAULT_NLU_FALLBACK_INTENT_NAME
from rasa.core.constants import (
    DEFAULT_NLU_FALLBACK_THRESHOLD,
    DEFAULT_NLU_FALLBACK_AMBIGUITY_THRESHOLD,
)
from rasa.nlu.classifiers.classifier import IntentClassifier
from rasa.nlu.selectors.response_selector import ResponseSelector
from rasa.nlu.components import Component
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.constants import (
    INTENT,
    INTENT_NAME_KEY,
    INTENT_RANKING_KEY,
    PREDICTED_CONFIDENCE_KEY,
    RESPONSE_SELECTOR,
    RESPONSE,
    INTENT_RESPONSE_KEY
)

RETRIEVAL_INTENT = 'faq'
RETRIEVAL_INTENT_KEY = 'retrieval_intent'  
THRESHOLD_KEY = "threshold"
AMBIGUITY_THRESHOLD_KEY = "ambiguity_threshold"

logger = logging.getLogger(__name__)


class FallbackResponseSelector(IntentClassifier):

    # please make sure to update the docs when changing a default parameter
    name = 'fallback'
    
    defaults = {
        # If all intent confidence scores are beyond this threshold, set the current
        # intent to `FALLBACK_INTENT_NAME`
        THRESHOLD_KEY: DEFAULT_NLU_FALLBACK_THRESHOLD,
        # If the confidence scores for the top two intent predictions are closer than
        # `AMBIGUITY_THRESHOLD_KEY`, then `FALLBACK_INTENT_NAME ` is predicted.
        AMBIGUITY_THRESHOLD_KEY: DEFAULT_NLU_FALLBACK_AMBIGUITY_THRESHOLD,
        RETRIEVAL_INTENT_KEY: None
    }

    @classmethod
    def required_components(cls) -> List[Type[Component]]:
        return [IntentClassifier, ResponseSelector]

    def process(self, message: Message, **kwargs: Any) -> None:
        """Process an incoming message.
        This is the components chance to process an incoming
        message. The component can rely on
        any context attribute to be present, that gets created
        by a call to :meth:`rasa.nlu.components.Component.create`
        of ANY component and
        on any context attributes created by a call to
        :meth:`rasa.nlu.components.Component.process`
        of components previous to this one.
        Args:
            message: The :class:`rasa.shared.nlu.training_data.message.Message` to
            process.
        """
        
        print('self.component_config: ',self.component_config)
        print('self.component_config: ',kwargs)
        if not self._should_fallback(message):
            return


        # we assume that the fallback confidence is the same as the fallback threshold
        confidence = self.component_config[THRESHOLD_KEY]
        message.data[INTENT] = _fallback_intent(confidence)
        message.data.setdefault(INTENT_RANKING_KEY, [])
        message.data[INTENT_RANKING_KEY].insert(0, _fallback_intent(confidence))

    def _should_fallback(self, message: Message) -> bool:
        """Check if the fallback intent should be predicted.
        Args:
            message: The current message and its intent predictions.
        Returns:
            `True` if the fallback intent should be predicted.
        """
        intent_name = message.data[INTENT].get(INTENT_NAME_KEY)
        below_threshold, nlu_confidence = self._nlu_confidence_below_threshold(message)

        if below_threshold:
            logger.debug(
                f"NLU confidence {nlu_confidence} for intent '{intent_name}' is lower "
                f"than NLU threshold {self.component_config[THRESHOLD_KEY]:.2f}."
            )
            return True

        ambiguous_prediction, confidence_delta = self._nlu_prediction_ambiguous(message)
        if ambiguous_prediction:
            logger.debug(
                f"The difference in NLU confidences "
                f"for the top two intents ({confidence_delta}) is lower than "
                f"the ambiguity threshold "
                f"{self.component_config[AMBIGUITY_THRESHOLD_KEY]:.2f}. Predicting "
                f"intent '{DEFAULT_NLU_FALLBACK_INTENT_NAME}' instead of "
                f"'{intent_name}'."
            )
            return True

        if intent_name ==  RETRIEVAL_INTENT and self._should_fallback_response_selector(message):
            return true
            
        return False

    def _should_fallback_response_selector(self, message: Message) -> bool:
        """Check if the fallback intent should be predicted.
        Args:
            message: The current message and its intent predictions.
        Returns:
            `True` if the fallback intent should be predicted.
        """
        retrieval_intent = self.component_config[RETRIEVAL_INTENT_KEY]
        intent_response_key =  message.data[RESPONSE_SELECTOR][retrieval_intent][RESPONSE].get(INTENT_RESPONSE_KEY)
        below_threshold, nlu_confidence = self._nlu_confidence_below_threshold_response_selector(message)

        if below_threshold:
            logger.debug(
                f"NLU confidence {nlu_confidence} for the intent response key '{intent_response_key}' is lower "
                f"than NLU threshold {self.component_config[THRESHOLD_KEY]:.2f}."
            )
            return True

        ambiguous_prediction, confidence_delta = self._nlu_prediction_ambiguous_response_selector(message)
        if ambiguous_prediction:
            logger.debug(
                f"The difference in NLU confidences "
                f"for the top two intents ({confidence_delta}) is lower than "
                f"the ambiguity threshold "
                f"{self.component_config[AMBIGUITY_THRESHOLD_KEY]:.2f}. Predicting "
                f"intent '{DEFAULT_NLU_FALLBACK_INTENT_NAME}' instead of "
                f"'{intent_response_key}'."
            )
            return True

        return False

    def _nlu_confidence_below_threshold(self, message: Message) -> Tuple[bool, float]:
        nlu_confidence = message.data[INTENT].get(PREDICTED_CONFIDENCE_KEY)
        return nlu_confidence < self.component_config[THRESHOLD_KEY], nlu_confidence

    def _nlu_confidence_below_threshold_response_selector(self, message: Message) -> Tuple[bool, float]:
        retrieval_intent = self.component_config[RETRIEVAL_INTENT_KEY]
        nlu_confidence = message.data[RESPONSE_SELECTOR][retrieval_intent][RESPONSE].get(PREDICTED_CONFIDENCE_KEY)
        return nlu_confidence < self.component_config[THRESHOLD_KEY], nlu_confidence

    def _nlu_prediction_ambiguous(
        self, message: Message
    ) -> Tuple[bool, Optional[float]]:
        intents = message.data.get(INTENT_RANKING_KEY, [])
        if len(intents) >= 2:
            first_confidence = intents[0].get(PREDICTED_CONFIDENCE_KEY, 1.0)
            second_confidence = intents[1].get(PREDICTED_CONFIDENCE_KEY, 1.0)
            difference = first_confidence - second_confidence
            return (
                difference < self.component_config[AMBIGUITY_THRESHOLD_KEY],
                difference,
            )
        return False, None

    def _nlu_prediction_ambiguous_response_selector(
        self, message: Message
    ) -> Tuple[bool, Optional[float]]:
        retrieval_intent = self.component_config[RETRIEVAL_INTENT_KEY]
        responses = message.data[RESPONSE_SELECTOR][retrieval_intent].get('ranking', [])
        if len(responses) >= 2:
            first_confidence = responses[0].get(PREDICTED_CONFIDENCE_KEY, 1.0)
            second_confidence = responses[1].get(PREDICTED_CONFIDENCE_KEY, 1.0)
            difference = first_confidence - second_confidence
            return (
                difference < self.component_config[AMBIGUITY_THRESHOLD_KEY],
                difference,
            )
        return False, None

def _fallback_intent(confidence: float) -> Dict[Text, Union[Text, float]]:
    return {
        INTENT_NAME_KEY: DEFAULT_NLU_FALLBACK_INTENT_NAME,
        PREDICTED_CONFIDENCE_KEY: confidence,
    }


def is_fallback_classifier_prediction(prediction: Dict[Text, Any]) -> bool:
    """Checks if the intent was predicted by the `FallbackClassifier`.
    Args:
        prediction: The prediction of the NLU model.
    Returns:
        `True` if the top classified intent was the fallback intent.
    """
    return (
        prediction.get(INTENT, {}).get(INTENT_NAME_KEY)
        == DEFAULT_NLU_FALLBACK_INTENT_NAME
    )


def undo_fallback_prediction(prediction: Dict[Text, Any]) -> Dict[Text, Any]:
    """Undo the prediction of the fallback intent.
    Args:
        prediction: The prediction of the NLU model.
    Returns:
        The prediction as if the `FallbackClassifier` wasn't present in the pipeline.
        If the fallback intent is the only intent, return the prediction as it was
        provided.
    """
    intent_ranking = prediction.get(INTENT_RANKING_KEY, [])
    if len(intent_ranking) < 2:
        return prediction

    prediction = copy.deepcopy(prediction)
    prediction[INTENT] = intent_ranking[1]
    prediction[INTENT_RANKING_KEY] = prediction[INTENT_RANKING_KEY][1:]

    return prediction