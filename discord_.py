import discord
from rasa_api import call_for
token = 'your token'
client = discord.Client()
@client.event  
async def on_ready():  
    print('Ready to talk to bot') 

@client.event
async def on_message(message):
    print(f"{message.author}, {message.content}")
    message_info = call_for(message.content)
    data = message_info['intent']['name']
    if message.author == client.user:
        return
    # check confidence here
    if data == 'greet':
        return await message.channel.send(f'{message.author.mention} Hey! welcome to python.learning')
    elif data == 'goodbye':
        return await message.channel.send(f'Bye, See you later, {message.author.mention}')
    elif data == 'abuse':
        return await message.channel.send(f'{message.author.mention} It\'s a warning, don\'t use abusive words')
    elif data == 'machine_learning':
        return await message.channel.send('YouTubers: \n0. Siraj Raval \n1. PyData \nBooks:\n0. Programming Collective Intelligence: Building Smart Web 2.0 Applications - Toby Segaran \n1. Building Machine Learning Systems with Python - Willi Richert, Luis Pedro Coelho \n2. Learning scikit-learn: Machine Learning in Python - Raúl Garreta, Guillermo Moncecchi \n3. Machine Learning in Action - Peter Harrington')
    elif data == 'youtube':
        return await message.channel.send('For Machine Learning:\n0. Siraj Raval https://www.youtube.com/channel/UCWN3xxRkmTPmbKwht9FuE5A \n1. Data School https://www.youtube.com/channel/UCnVzApLJE2ljPZSeQylSEyg \n2. Krista King https://www.youtube.com/user/TheIntegralCALC \n3. Professor Leonard https://www.youtube.com/user/professorleonard57 \n4. PyData https://www.youtube.com/user/PyDataTV \n5. The SemiColon https://www.youtube.com/channel/UCwB7HrnRlOfasrbCJoiZ9Lg')
    elif data == 'book':
        return await message.channel.send('For Beginners:\n0. Learning Python: Book by David Ascher and Mark Lutz \n1. Python Cookbook: Book by Alex Martelli and others \n2. Learn Python the Hard Way: Book by Zed Shaw \n3. Head First Python : Paul Barry')
    else:
        return await message.channel.send(f'We will contact you for the query "{message.content}"')

client.run(token)