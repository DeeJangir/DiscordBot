def call():
    from rasa_nlu.training_data import load_data
    from rasa_nlu import config
    from rasa_nlu.components import ComponentBuilder
    from rasa_nlu.model import Trainer

    builder = ComponentBuilder(use_cache=True)

    training_data = load_data('./rasa_nlu_api/dataset.json')
    trainer = Trainer(config.load("./rasa_nlu_api/config_pipeline.yml"), builder)
    trainer.train(training_data)
    model_directory = trainer.persist('./rasa_nlu_api/', fixed_model_name="model")
    print('done')
    return model_directory

# call()
def call_for(message):
    # from datetime import datetime
    # s = datetime.now()
    from rasa_nlu.model import Interpreter
    interpreter = Interpreter.load('rasa_nlu_api/default/model')
    # from random import choice
    # from json import loads
    import spacy
    nlp = spacy.load('./en')
    parsed_sentence = interpreter.parse(message)
    return parsed_sentence
    # print(datetime.now()-s)
# print(call_for('fuck off')['intent']['name'])