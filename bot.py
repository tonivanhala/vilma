# -*- coding: utf-8 -*-
from py2neo import Graph, Path, Node, Relationship, authenticate, ServiceRoot
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.client import HTTPSConnection
from urllib.parse import parse_qs, urlencode
import json

import re, os, random, math

TARGET_CHANNEL = u'vilma'

SLACK_INCOMING_WEBHOOK_HOST = u'hooks.slack.com'
SLACK_INCOMING_WEBHOOK_PATH = u'<Insert path with token here>'

word_pattern = re.compile(r'\s*([a-zA-ZåäöÅÄÖ]+)')

#authenticate("localhost:7474", "neo4j", "password")
#graph = Graph("http://localhost:7474/db/data/")

graphenedb_url = os.environ.get("GRAPHENEDB_URL", "http://localhost:7474/")
graph = ServiceRoot(graphenedb_url).graph

STOP_WORDS = [u"on", u"ei", u"kyllä", u"olla", u"jonka", u"että", u"jotta", u"koska", u"kuinka", u"jos", u"vaikka", u"kuin", u"kunnes", u"mutta", u"no", u"ehkä"]
REPLACEMENTS = {
	u"sinä":u"minä",
	u"sinun":u"minun",
	u"minä":u"sinä",
	u"minun":u"sinun",
	u"meidän":u"teidän",
	u"teidän":u"meidän",
	u"olet":u"olen",
	u"olen":u"olet",
	u"mä":u"sä",
	u"sä":u"mä",
	u"mää":u"sää",
	u"sää":u"mää",
	u"miksi":u"siksi",
	u"milloin":u"silloin",
	u"siksi":u"miksi",
	u"silloin":u"milloin",
	u"miten":u"siten",
	u"sinusta":u"minusta",
	u"minusta":u"sinusta"
}

PORT = os.environ.get('PORT', 8000)



def train(message):
	words = word_pattern.findall(message)
	prev = None
	for i in range(0,len(words)-1):
		first_word = words[i].lower()
		second_word = words[i+1].lower()
		if first_word in REPLACEMENTS.keys():
			word = REPLACEMENTS[first_word]
		if second_word in REPLACEMENTS.keys():
			word = REPLACEMENTS[second_word]	
		pair = u"{0}_{1}".format(first_word, second_word)
		tail_node = graph.merge_one("Wordpair", "wordpair", pair)
		tail_node.properties['first'] = first_word
		tail_node.properties['second'] = second_word
		tail_node.push()
		if prev is not None:
			(prev_first, prev_second) = prev
			prev_pair = u"{0}_{1}".format(prev_first, prev_second)
			head_node = graph.merge_one("Wordpair", "wordpair", prev_pair)
			head_node.properties['first'] = prev_first
			head_node.properties['second'] = prev_second
			head_node.push()
			cypher = u"""MATCH (a:Wordpair), (b:Wordpair) WHERE a.wordpair={first_pair} AND b.wordpair={second_pair}
			MERGE (a)-[new:Link]->(b) on create set new.weight=0 RETURN new"""
			result = graph.cypher.execute(cypher, {"first_pair": prev_pair, "second_pair": pair})
			result.one.properties['weight'] += 1
			result.one.push()
		prev = (first_word, second_word)

def generate_backward(word):
	words = []
	#first, choose a Node to start at
	cypher_incoming_weights = """MATCH (:Wordpair)-[link:Link]->(a:Wordpair) WHERE a.second={word} RETURN a, sum(link.weight) AS weight ORDER BY weight DESC"""
	result = graph.cypher.execute(cypher_incoming_weights, {"word": word})
	cypher_total_weights = """MATCH (:Wordpair)-[link:Link]->(a:Wordpair) WHERE a.second={word} RETURN sum(link.weight) AS weight"""
	result_total = graph.cypher.execute(cypher_total_weights, {"word": word})
	node = None
	total = result_total.one
	if total is not None and total > 0:
		i = random.randint(1, total)
		for row in result:
			i = i - row.weight
			node = row.a
			if i <= 0:
				break
	if node is None: 
		cypher_random = """MATCH (a:Wordpair) WITH a, rand() AS number RETURN a ORDER BY number LIMIT 1"""
		result_random = graph.cypher.execute(cypher_random)
		node = result_random.one
	words.append(node['first'])
	alpha = 1.
	while random.random() < alpha:
		pair = u"{0}_{1}".format(node['first'], node['second'])
		cypher_previous_nodes = """MATCH (b:Wordpair)-[link:Link]->(a:Wordpair) WHERE a.wordpair={pair} RETURN b, link.weight AS weight ORDER BY weight DESC"""
		result = graph.cypher.execute(cypher_previous_nodes, {"pair": pair})
		cypher_total_weights = """MATCH (:Wordpair)-[link:Link]->(a:Wordpair) WHERE a.wordpair={pair} RETURN sum(link.weight) AS weight"""
		total = graph.cypher.execute(cypher_total_weights, {"pair": pair}).one
		fraction = random.random()
		if total is not None and total > 0:
			i = fraction * total
			for row in result:
				i = i - row.weight
				node = row.b
				if i <= 0:
					break
		words[0:0] = [node['first']]
		alpha -= fraction
	return words

def generate_forward(word):
	words = []
	#first, choose a Node to start at
	cypher_outgoing_weights = """MATCH (a:Wordpair)-[link:Link]->(:Wordpair) WHERE a.first={word} RETURN a, sum(link.weight) AS weight ORDER BY weight DESC"""
	result = graph.cypher.execute(cypher_outgoing_weights, {"word": word})
	cypher_total_weights = """MATCH (a:Wordpair)-[link:Link]->(:Wordpair) WHERE a.first={word} RETURN sum(link.weight) AS weight"""
	result_total = graph.cypher.execute(cypher_total_weights, {"word": word})
	node = None
	total = result_total.one
	if total is not None and total > 0:
		i = random.randint(1, total)
		for row in result:
			i = i - row.weight
			node = row.a
			if i <= 0:
				break
	if node is None: 
		cypher_random = """MATCH (a:Wordpair) WITH a, rand() AS number RETURN a ORDER BY number LIMIT 1"""
		result_random = graph.cypher.execute(cypher_random)
		node = result_random.one
	words.append(node['second'])
	alpha = 1.
	while random.random() < alpha:
		pair = u"{0}_{1}".format(node['first'], node['second'])
		cypher_previous_nodes = """MATCH (a:Wordpair)-[link:Link]->(b:Wordpair) WHERE a.wordpair={pair} RETURN b, link.weight AS weight ORDER BY weight DESC"""
		result = graph.cypher.execute(cypher_previous_nodes, {"pair": pair})
		cypher_total_weights = """MATCH (a:Wordpair)-[link:Link]->(:Wordpair) WHERE a.wordpair={pair} RETURN sum(link.weight) AS weight"""
		total = graph.cypher.execute(cypher_total_weights, {"pair": pair}).one
		fraction = random.random()
		if total is not None and total > 0:
			i = fraction * total
			for row in result:
				i = i - row.weight
				node = row.b
				if i <= 0:
					break
		words.append(node['second'])
		alpha -= fraction
	return words

def generate_replies(message):
	words = word_pattern.findall(message)
	prev = None
	replies = []
	#build response for each word of the message
	for word in words:
		if word in STOP_WORDS:
			continue
		if word in REPLACEMENTS.keys():
			word = REPLACEMENTS[word]
		begin = generate_backward(word)
		end = generate_forward(word)
		replies.append(u"{0} {1} {2}".format(u" ".join(begin), word, u" ".join(end)))
	return replies

def compute_entropy(reply):
	entropy = 0.
	words = reply.split(" ")
	for i in range(0,len(words)-1):
		pair = u"{0}_{1}".format(words[i], words[i+1])
		cypher_total_weights = """MATCH (:Wordpair)-[link:Link]->(a:Wordpair) WHERE a.wordpair={pair} RETURN sum(link.weight) AS weight"""
		result_total = graph.cypher.execute(cypher_total_weights, {"pair": pair})
		total = result_total.one
		if total is not None and total > 0:
			inverse = 100 - total
			if inverse < 1:
				inverse = 1
			entropy += math.log(inverse)
	return entropy

class RequestHandler(BaseHTTPRequestHandler):
	def do_POST(self):
		content_len = int(self.headers.get('content-length',0))
		#post_body = self.rfile.read(content_len).decode('utf-8')
		#post_body = post_body.lower()
		#content_len = int(self.headers.get('content-length',0))
		post_body = self.rfile.read(content_len)
		postvars = parse_qs(post_body.decode('ASCII'))
		#for key in postvars.keys():
		#	print(key, postvars[key])
		token = postvars.get(b'token', None)
		if token is None:
			token = postvars.get(u'token', None)
		msg = postvars.get(b'text', None)
		if msg is None:
			msg = postvars.get(u'text', None)
		username = postvars.get(b'user_name', None)
		if username is None:
			username = postvars.get(u'user_name', None)
		channel_name = postvars.get(b'channel_name', None)
		if channel_name is None:
			channel_name = postvars.get(u'channel_name', None)
		self.send_response(200)
		#self.send_header("Access-Control-Allow-Origin", "*")
		self.end_headers()
		message = msg[0]
		print(u"Message was: {0}".format(message))
		replies = []
		replies = generate_replies(message)
		if len(replies) > 0:
			entropies = [(reply, compute_entropy(reply)) for reply in replies]
			entropies = sorted(entropies, key = lambda x: -x[1])
			total_entropy = 0.
			for (reply, entropy) in entropies:
				total_entropy += entropy
			i = random.uniform(0, total_entropy)
			selected = None
			for (reply, entropy) in entropies:
				selected = reply
				i -= entropy
				if i <= 0:
					break
			if selected is None:
				selected = entropies[0][0]
			payload = { "text" : message, "username": u"VILMA", "channel": u"#{0}".format(TARGET_CHANNEL), "icon_url": u"https://i1.wp.com/www.vincit.fi/wordpress/wp-content/uploads/2015/04/roboduck05.png" }
			connection = HTTPSConnection(SLACK_INCOMING_WEBHOOK_HOST)
			connection.request("POST", SLACK_INCOMING_WEBHOOK_PATH, json.dumps(payload))
			response = connection.getresponse()
		train(message)

handler_class = RequestHandler
int_port = int(PORT)
server_address = ('', int_port)
httpd = HTTPServer(server_address, handler_class)
httpd.serve_forever()
