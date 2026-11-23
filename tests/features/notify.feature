Feature: Storage's notices: a signed word that a file arrived in a buffer's inbox

  POST /ingest/notify carries {"tenant", "name", "size"}, signed with INGEST_NOTIFY_KEY
  over "<timestamp>." and the body. The pod's clock stands at the moment 1800000000,
  and the scanner scans the call log calls_visit_ee and the two buffers that share the
  tenant ci-usage. A notice the pod takes hands the scanner the buffer, name and size.

  Scenario: A signed notice hands the scanner its file
    Given the pod's notify key is "k1"
    When storage sends, signed with "k1" at the moment 1800000000:
      """
      {"tenant": "calls-visit-ee", "name": "inbox/calls/2027-01-15/075001-app-1.avro", "size": 812}
      """
    Then the answer is 202
    And the answer is the JSON:
      """
      {"buffer": "calls_visit_ee"}
      """
    And the scanner was handed:
      """
      [["calls_visit_ee", "inbox/calls/2027-01-15/075001-app-1.avro", 812]]
      """

  Scenario Outline: A tenant two buffers share hands the file to the one whose folder it is in
    Given the pod's notify key is "k1"
    When storage sends, signed with "k1" at the moment 1800000000:
      """
      {"tenant": "ci-usage", "name": "<name>", "size": 1}
      """
    Then the answer is 202
    And the answer includes "<buffer>"

    Examples:
      | name                                         | buffer                 |
      | inbox/ci-jobs/2027-01-15/024100-ci-1.avro    | platform.ci_jobs       |
      | inbox/cluster-usage/2027-01-15/0240-u-1.avro | platform.cluster_usage |

  Scenario: Without a key every notice is refused
    When storage sends, signed with "k1" at the moment 1800000000:
      """
      {"tenant": "calls-visit-ee", "name": "inbox/calls/2027-01-15/075001-app-1.avro", "size": 812}
      """
    Then the answer is 403
    And the answer includes "INGEST_NOTIFY_KEY"
    And the scanner was handed nothing

  Scenario: An app made without notices refuses them too
    Given the pod has no notices
    When storage sends, signed with "k1" at the moment 1800000000:
      """
      {"tenant": "calls-visit-ee", "name": "inbox/calls/2027-01-15/075001-app-1.avro", "size": 812}
      """
    Then the answer is 403

  Scenario Outline: A notice signed wrong, or not at all, or too far from now, is refused
    Given the pod's notify key is "k1"
    When storage sends, signed with "<key>" at the moment <moment>:
      """
      {"tenant": "calls-visit-ee", "name": "inbox/calls/2027-01-15/075001-app-1.avro", "size": 812}
      """
    Then the answer is 401
    And the answer includes "<why>"
    And the scanner was handed nothing

    Examples:
      | key | moment     | why                    |
      | k2  | 1800000000 | isn't this body's      |
      | k1  | 1799999699 | more than 300 seconds  |
      | k1  | 1800000301 | more than 300 seconds  |

  Scenario: A notice signed 300 seconds ago is still taken
    Given the pod's notify key is "k1"
    When storage sends, signed with "k1" at the moment 1799999700:
      """
      {"tenant": "calls-visit-ee", "name": "inbox/calls/2027-01-15/075001-app-1.avro", "size": 812}
      """
    Then the answer is 202

  Scenario: A notice with no signature is refused
    Given the pod's notify key is "k1"
    When I POST to "/ingest/notify" with:
      """
      {"tenant": "calls-visit-ee", "name": "inbox/calls/2027-01-15/075001-app-1.avro", "size": 812}
      """
    Then the answer is 401
    And the answer includes "X-Ingest-Signature"

  Scenario: A signed body changed on the way is refused
    Given the pod's notify key is "k1"
    When storage sends a body other than the one it signed with "k1"
    Then the answer is 401

  Scenario Outline: A notice that isn't one is refused
    Given the pod's notify key is "k1"
    When storage sends, signed with "k1" at the moment 1800000000:
      """
      <body>
      """
    Then the answer is <status>
    And the answer includes "<why>"
    And the scanner was handed nothing

    Examples:
      | body                                                                                  | status | why                    |
      | not json                                                                              | 400    | the body is JSON       |
      | ["calls-visit-ee"]                                                                    | 400    | the body is {          |
      | {"tenant": "calls-visit-ee", "name": "inbox/calls/a.avro", "size": "812"}             | 400    | the body is {          |
      | {"tenant": "calls-visit-ee", "name": "inbox/calls/a.avro", "size": -1}                | 400    | the body is {          |
      | {"tenant": "calls-visit-ee", "name": "inbox/calls/a.avro", "size": 52428801}          | 400    | the body is {          |
      | {"tenant": "calls-visit-ee", "name": "batches/inbox_1.json", "size": 3}               | 422    | is no file in an inbox |
      | {"tenant": "calls-visit-ee", "name": "inbox/../f/x.avro", "size": 3}                  | 422    | is no file in an inbox |
      | {"tenant": "calls-other", "name": "inbox/calls/a.avro", "size": 3}                    | 422    | no buffer here takes   |
      | {"tenant": "calls-visit-ee", "name": "inbox/signals/a.avro", "size": 3}               | 422    | no buffer here takes   |

  Scenario: Storage's notices reach the ingest pod, and its key comes from the declared Secret
    When I read how a notice reaches the ingest pod in k8s/ and pubrules.json
    Then the result is:
      """
      {"ingress path": "/ingest", "ingress service": "ingest",
       "service selects the pod": true, "service port": "http",
       "the policy admits Traefik": true, "the policy admits the service": true,
       "INGEST_NOTIFY_KEY": {"name": "ingest-notify", "key": "key", "optional": true},
       "declared secret": {"name": "ingest-notify", "key": "key"}}
      """
