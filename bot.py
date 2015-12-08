# -*- coding: utf-8 -*-
from py2neo import Graph, Path, Node, Relationship, authenticate, ServiceRoot
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.client import HTTPSConnection
from urllib.parse import parse_qs, urlencode
import json

import re, os, random, math

from Levenshtein import _levenshtein

PORT = os.environ.get('PORT', 8000)
NEO4J_URL = os.environ.get('NEO4J_URL', 'http://localhost:7474/db/data/')

TARGET_CHANNEL = u'vilma'

SLACK_INCOMING_WEBHOOK_HOST = u'hooks.slack.com'
SLACK_INCOMING_WEBHOOK_PATH = u'<Insert path with token here>'

word_pattern = re.compile(r'\s*([a-zA-ZåäöÅÄÖ]+)')

#authenticate("localhost:7474", "neo4j", "password")
#graph = Graph("http://localhost:7474/db/data/")

#authenticate("localhost:7474", "neo4j", "password")
graph = Graph(NEO4J_URL)

#graphenedb_url = os.environ.get("GRAPHENEDB_URL", "http://localhost:7474/")
#graph = ServiceRoot(graphenedb_url).graph

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
	u"minusta":u"sinusta", 
	u"et": u"en",
	u"en": u"et",
	u"ette" : u"emme",
	u"emme": u"ette"
}


class CypherBuilder:
	def __init__(self, forward=True):
		self.word = False
		self.wordpair = False
		self.group_by_node = False
		self.forward = forward
	def with_word(self, option = True):
		self.word = option
		self.wordpair = not option
		return self
	def with_wordpair(self, option = True):
		self.wordpair = option
		self.word = not option
		return self
	def group(self, option = True):
		self.group_by_node = option
		return self
	def build(self):
		cypher = u"MATCH (a:Wordpair)-[link:Link]->(b:Wordpair)"
		if self.word:
			cypher += u" WHERE {0}.{1}={{word}}".format(u'a' if self.forward else u'b', u'first' if self.forward else u'second')
		elif self.wordpair: 
			cypher += u" WHERE {0}.wordpair={{pair}}".format(u'a' if self.forward else u'b')
		else:
			return None
		if self.group_by_node:
			if self.forward and self.word:
				cypher += u" RETURN a, sum(link.weight) AS weight ORDER BY weight DESC"
			elif self.forward and self.wordpair:
				cypher += u" RETURN b, sum(link.weight) AS weight ORDER BY weight DESC"
			elif self.word:
				cypher += u" RETURN b, sum(link.weight) AS weight ORDER BY weight DESC"
			else: 
				cypher += u" RETURN a, sum(link.weight) AS weight ORDER BY weight DESC"
		else:
			cypher += u" RETURN sum(link.weight) AS weight"
		return cypher


def train_input(message):
	words = word_pattern.findall(message)
	if len(words) < 2:
		return
	prev = None
	for i in range(0,len(words)-1):
		first_word = words[i].lower()
		second_word = words[i+1].lower()
		if first_word in REPLACEMENTS.keys():
			first_word = REPLACEMENTS[first_word]
		if second_word in REPLACEMENTS.keys():
			second_word = REPLACEMENTS[second_word]	
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

def pick_start_node(word, forward = True):
	builder = CypherBuilder(forward)
	result = graph.cypher.execute(builder.with_word().group().build(), {"word": word})
	result_total = graph.cypher.execute(builder.with_word().group(False).build(), {"word": word})
	node = None
	total = result_total.one
	if total is not None and total > 0:
		i = random.randint(1, total)
		for row in result:
			i = i - row.weight
			node = row.a if forward else row.b
			if i <= 0:
				break
	if node is not None:
		return (0, node)
	cypher_random = """MATCH (a:Wordpair) WITH a, rand() AS number RETURN a ORDER BY number LIMIT 10"""
	result_random = graph.cypher.execute(cypher_random)
	distances = []
	for row in result_random:
		d = _levenshtein.distance(row.a.properties['first'], word)
		distances.append((d, row.a))
	return sorted(distances, key = lambda x: x[0])[0] 

def recursive_generation(node, alpha, forward = True):
	if random.random() >= alpha:
		return [node]
	pair = u"{0}_{1}".format(node['first'], node['second'])
	builder = CypherBuilder(forward)
	linked_nodes = graph.cypher.execute(builder.with_wordpair().group().build(), {"pair": pair})
	total_weight = graph.cypher.execute(builder.with_wordpair().group(False).build(), {"pair": pair}).one
	if total_weight is None or total_weight < 1:
		return [node]
	fraction = random.random()
	i = fraction * total_weight
	for row in linked_nodes:
		i = i - row.weight
		new_node = row.b if forward else row.a 
		if i <= 0:
			break
	nodes = recursive_generation(new_node, alpha - fraction, forward)
	if forward:
		return [node] + nodes
	return nodes + [node]

def generate_backward(word):
	distance, node = pick_start_node(word, False)
	return (distance, recursive_generation(node, 1., forward = False))

def generate_forward(word):
	distance, node = pick_start_node(word, True)
	return (distance, recursive_generation(node, 1., forward = True))

def unwrap_sentence(nodes):
	words = []
	for node in nodes:
		words.append( node['first'] )
	words.append( node['second'] )
	return u" ".join(words)

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
		distance_1, begin = generate_backward(word)
		distance_2, end = generate_forward(word)
		replies.append( (distance_1 + distance_2, unwrap_sentence(begin + end) ) )
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

def extract_postvars(postvars, *args):
	vals = []
	for x in args:
		val = postvars.get(x, None)
		if val is None:
			val = postvars.get(x.decode('utf-8'), None)
		vals.append(val)
	return vals

class RequestHandler(BaseHTTPRequestHandler):
	def do_POST(self): 
		content_len = int(self.headers.get('content-length',0))
		post_body = self.rfile.read(content_len)
		postvars = parse_qs(post_body.decode('ASCII'))
		#print(postvars)
		(token, msg, username, channel_name, train) = extract_postvars(postvars, b'token', b'text', b'user_name', b'channel_name', b'train')
		self.send_response(200)
		self.end_headers()
		if username is not None and (username[0] == u'VILMA' or username[0] == u'slackbot'):
			return
		message = msg[0]
		replies = []
		replies = generate_replies(message)
		if len(replies) > 0:
			entropies = [(reply, compute_entropy(reply) / (distance + 1) ) for distance, reply in replies]
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
			if train is None:
				payload = { "text" : selected, "username": u"VILMA", "channel": u"#{0}".format(TARGET_CHANNEL), "icon_url": u"https://i1.wp.com/www.vincit.fi/wordpress/wp-content/uploads/2015/04/roboduck05.png" }
				connection = HTTPSConnection(SLACK_INCOMING_WEBHOOK_HOST)
				connection.request("POST", SLACK_INCOMING_WEBHOOK_PATH, json.dumps(payload))
				response = connection.getresponse()
			else:
				#self.send_header("Access-Control-Allow-Origin", "*")
				#self.end_headers()
				self.wfile.write(u'{{"message": "{0}"}}'.format(selected).encode('utf-8'))
		train_input(message)

handler_class = RequestHandler
int_port = int(PORT)
server_address = ('', int_port)
httpd = HTTPServer(server_address, handler_class)
httpd.serve_forever()
